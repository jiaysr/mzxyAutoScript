# This Python file uses the following encoding: utf-8
"""
世界任务（WorldTask）：每天一次，只在开放世界时段（09:00-24:00）里跑

当前进度：**接 7 个跨服任务 + 进世界之巅 + 做采集类任务** —— 判断是否在 NPC 所在地图
（默认域外灵岛）→ 打开小地图把视口拖到右上角 → 点录好的 NPC 点位 → **主动关掉小地图** →
用「人物坐标有没有变化」判断这一下点没点中（没动且没对话框就重试）→ 对话框出来后用模板确认是哪个 NPC
→ 在守门人对话里点「查看世界之巅活动」→ 活动弹窗打开 → 切到「跨服任务」tab
→ 按顺序接 7 个任务（接过的会被游戏移到列表末尾，所以每次接第一行）
→ 切回「世界之巅」tab 点「进入世界之巅」→ 等右上角地点变成「世界之巅」
→ 任务页里逐个做采集类任务（木料之争 / 世界之巅 / 世界印记）。
其余任务、重启回岛、交付后面接着补。

**重启上线会自动离开世界之巅**：人物上线时会被送回域外灵岛，所以每次要做图里的任务之前
都要 `ensure_world_peak_map()` 确认一次（不在就重走「守门人 → 活动弹窗 → 进入世界之巅」）；
已经在地图里（上一轮没做完）则跳过接取阶段直接做任务。

任务完成检测（接任务之前就要开着）：
    左下角聊天栏会打 `[系]任务：【跨服】世界之巅已完成`，`monitor.py` 用「[系]任务」前缀模板
    做一级过滤、命中后再 OCR 抽任务名；挂在本任务的每次截图上，所以接任务、走图全程都不漏。
    接取时有些任务会当场判定完成，这些名字先记下来，后面就不用再去世界之巅做。

游戏里的页面链路（2026-11-05 录制，见 tasks/WorldTask/res/）：
    守门人对话（I_NEW）→「查看世界之巅活动」选项行（I_ITEM_3）→ 世界之巅活动弹窗
    →「世界之巅」tab 有黄帝立绘（I_ITEM_4）→「跨服任务」tab（O_NEW 读 tab 名，S_NEW 滑 tab 条）
    → 任务列表：左边任务名（O_TASK_LIST），右边每行一个「接取」按钮（I_ITEM_5）
    活动时间：每周星期一到星期五 9:00-24:00（与 schedule.py 的窗口一致）

接取时的加量推广弹窗（I_PROMO_CLOSE / O_BONUS_PROMO）：
    点「接取」会弹「当前任务加量功能未激活…」，它盖在列表上会吞掉后续点击。
    处理方式与 AutoTask.click_auto_start 一致：出现就点绿X关掉，**不勾「今日不再提示」**
    （勾了就改动了玩家的游戏设置）。

为什么点完要主动关小地图：点位点击不一定生效（地图没拖到位、点在遮罩上都会吃掉），
小地图开着又正好盖住对话框，只会白等到超时。所以关掉之后拿主页面右上角的坐标当判据：
坐标动了 = 点中了、人物在走过去；坐标不动、对话框也不出 = 这一下没生效，重试。

排期（与玩法无关，先看这里）：
- 下次运行时间 = 现在 + 成功/失败间隔，再夹进开放世界时段（09:00-24:00），见 schedule.py；
- 写入走 server=False：默认路径在 server_update != 09:00 时会把时间整体覆盖掉；
- 被手动运行 / 别的任务 task_call 提前叫醒而门没开时，只重排到最近一次开门，不碰游戏。

NPC 素材（tasks/WorldTask/res/，标注工具录制）：
- 点位 C_NEW / C_ITEM_2 是按「小地图视口在右上角」录的，所以点之前必须先 map_pan_to_corner('top_right')；
- 对话框特征 I_NEW / I_ITEM_2 匹配的是标题 + 第一行台词，NPC 台词变了模板就会失效（届时重录即可）。
"""
import difflib
import re
from datetime import datetime

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.AutoTask.assets import AutoTaskAssets
from tasks.AutoTask.config import BonusMultiplier
from tasks.AutoTask.page import page_bonus
from tasks.AutoTask.script_task import ScriptTask as AutoTaskScriptTask
from tasks.Battle.slots import ACTION_BUTTON_DISC_CENTER
from tasks.Battle.target_action import ACTION_GATHER
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_task
from tasks.WorldTask.assets import WorldTaskAssets
from tasks.WorldTask.monitor import TaskCompleteMonitor
from tasks.WorldTask.schedule import clamp_to_open_world, in_open_world, next_run_after


class ScriptTask(GameUi, WorldTaskAssets, AutoTaskAssets):
    SCHEDULER_NAME = 'WorldTask'
    TASK_NAME = 'WorldTask'

    # 任务加量页的操作直接复用自动任务的实现（同一套页面、同一套按钮语义）
    read_bonus_counts = AutoTaskScriptTask.read_bonus_counts
    set_bonus_state = AutoTaskScriptTask.set_bonus_state
    set_bonus_side = AutoTaskScriptTask.set_bonus_side

    # 两个 NPC（名字对应 res/click.json 里的 itemName）
    NPC_NEW = 'new'        # 入口 NPC：世界之巅守门人（接任务 / 进世界之巅）
    NPC_ITEM_2 = 'item_2'  # 交付 NPC：世界之巅任务榜

    # 点完点位后，多久内人物坐标必须变化（否则认定这一下没点中）
    NPC_MOVE_TIMEOUT = 4
    # 判断「坐标变了」的容差（像素）
    NPC_MOVE_TOLERANCE = 3
    # 坐标变了之后，最多等多久对话框（人物在走过去）
    NPC_DIALOG_TIMEOUT = 20
    # 坐标停住多久还不出对话框，认定卡住
    NPC_STUCK_TIMEOUT = 5
    # 单次找 NPC 最多点几次
    NPC_RETRY = 3

    # 世界之巅活动弹窗顶部 tab：按顺序登记，用来判断该往哪个方向翻
    # 目前流程只用得到前两个（世界之巅 = 活动主页 / 跨服任务 = 接取列表）
    ACTIVITY_TABS = ('世界之巅', '周末限时', '跨服任务', '灭世铸烬',
                     '荣誉商店', '荣耀币商店', '玩法说明')
    # 世界之巅是第一个 tab：弹窗停在上次的 tab 时，只能往左翻回来
    ACTIVITY_HOME_TAB = '世界之巅'
    # 世界任务所在 tab
    ACTIVITY_TASK_TAB = '跨服任务'
    # 进图后右上角的地点名
    WORLD_PEAK_LOCATION = '世界之巅'

    # 世界之巅里的「采集类」任务：任务名 -> 点「寻路」后人物走到的完成点坐标
    # 三个任务玩法一样（锁定采集物 -> 点动作按钮 -> 采完继续），只是完成点不同
    GATHER_TASKS = (
        ('木料之争', (145, 470)),
        ('世界之巅', (302, 250)),
        ('世界印记', (428, 107)),
    )

    # 世界之巅里的「打怪类」任务：小地图拖到某个角落 -> 点刷新点 -> 走到打架位 -> 打指定怪
    # 玩法都一样，只有「锚点角 / 刷新点 / 打架位 / 目标名」不同
    FIGHT_TASKS = (
        # 任务名, 目标名, 打架位, 小地图锚点角, 小地图点位规则
        ('兽毛之争', '野兽', (226, 375), 'bottom_right'),
        ('黄金比例', '黄金鼠', (220, 156), 'top_left'),
    )
    FIGHT_DRIFT_CHECK = 5.0          # 打怪时每隔几秒查一次坐标
    FIGHT_DRIFT_TOLERANCE = 12       # 偏离打架位超过这个格数就回点位
    FIGHT_TRAVEL_TIMEOUT = 90        # 走过去 / 回点位的超时
    FIGHT_TIMEOUT = 600              # 单个任务打怪阶段的超时
    # 判定「走到目标点」的坐标容差
    WOOD_POS_TOLERANCE = 15

    # 世界之巅里的任务执行顺序（任务名, 玩法）
    # - gather：任务页点「寻路」-> 走到完成点 -> 循环采集
    # - fight ：小地图点刷新点 -> 走到打架位 -> 打指定怪
    # - glory ：为了荣耀，按阵营换目标/点位
    # - drop  ：世界之心，**没有自己的玩法** —— 前面打怪的掉落物，最后统一判断
    WORLD_PEAK_ORDER = (
        ('兽毛之争', 'fight'),
        ('木料之争', 'gather'),
        ('为了荣耀', 'glory'),
        ('世界之巅', 'gather'),
        ('黄金比例', 'fight'),
        ('世界印记', 'gather'),
        ('世界之心', 'drop'),
    )
    DROP_TASK = '世界之心'           # 靠打怪掉落的那个任务
    DROP_RETRY = 3                   # 还没掉到时最多重打几次「为了荣耀」

    # 世界之巅两个阵营：每天首次进图随机分配，出生点不同（真机实测）
    CAMP_HUANGDI = 'huangdi'
    CAMP_YANDI = 'yandi'
    CAMP_SPAWNS = {
        CAMP_HUANGDI: (56, 60),
        CAMP_YANDI: (439, 384),
    }
    CAMP_SPAWN_TOLERANCE = 45        # 出生点判定容差（走路会有点偏差）

    # 为了荣耀：同一套打怪流程，但阵营不同 -> 目标 / 打架位 / 小地图锚点 / 点位都不同
    GLORY_TASK = '为了荣耀'
    GLORY_VARIANTS = {
        # 阵营: (目标名, 打架位, 小地图锚点角, 小地图点位规则名)
        CAMP_HUANGDI: ('炎帝守门人', (400, 316), 'bottom_right', 'glory_huangdi'),
        CAMP_YANDI: ('黄帝守门人', (90, 128), 'top_left', 'glory_yandi'),
    }
    # 不知道自己是哪个阵营时，每个变体给多少秒去「锁到目标」，锁不到就换另一个
    GLORY_PROBE_LOCK_TIMEOUT = 25

    # 要接的跨服任务（列表里的先后顺序）
    # 接过的任务会被游戏移到列表末尾，所以「每次接第一行」就能按这个顺序接完
    ACCEPT_TASKS = ('世界之巅', '黄金比例', '木料之争', '兽毛之争',
                    '世界印记', '为了荣耀', '世界之心')

    def __init__(self, config, device):
        super().__init__(config, device)
        # 任务完成提示监测：接任务时 start()、接取的任务全部完成时 stop()
        self.task_monitor = TaskCompleteMonitor(self.I_TASK_COMPLETE_TRIGGER,
                                                self.O_TASK_COMPLETE)
        self.completed_tasks: list = []   # 监测到的「已完成」任务名
        self.accepted_tasks: list = []    # 本次接取到的任务名
        self.camp = None                  # 世界之巅阵营（进场时按出生点判断）
        # 逐行识别用的 OCR 引擎（rec-only 比整块 detect 快 4 倍以上）
        from module.ocr.models import get_local_ocr_model
        self._ocr = get_local_ocr_model()
        # 按帧缓存：同一帧上重复读列表 / 读 tab 条不再重新 OCR
        self._rows_cache: tuple = (None, [])
        self._tabs_cache: tuple = (None, [])
        # tab 名 -> 上次 OCR 到的坐标（切过 tab 没滑过 tab 条时，切别的 tab 直接点，省一次 900ms 的 OCR）
        self._tab_positions: dict = {}

    def screenshot(self):
        """
        截图 + （监测开着时）跑一遍任务完成提示监测

        提示是**最顶层弹窗**（任何界面都盖在上面），但只在左下角聊天行停留 1.5 秒，
        所以挂在每次截图上、并在点完「接取」后连拍 2 秒，才不会漏。
        """
        image = super().screenshot()
        if self.task_monitor.enabled:
            for name in self.task_monitor.detect(image):
                if name not in self.completed_tasks:
                    self.completed_tasks.append(name)
                logger.info(f'任务完成提示：{name}')
            if self.all_tasks_completed():
                logger.info('接取的任务已全部完成')
                self.task_monitor.stop()
        return image

    def all_tasks_completed(self) -> bool:
        """接取到的任务是不是全部识别到「已完成」"""
        if not self.accepted_tasks:
            return False
        for name in self.accepted_tasks:
            if not any(self.task_name_match(text, name) for text in self.completed_tasks):
                return False
        return True

    def watch(self, seconds: float = 2.0, interval: float = 0.2) -> None:
        """
        盯着屏幕连拍（完成提示只显示 1.5 秒，固定 sleep 很容易整段错过）
        """
        timer = Timer(seconds).start()
        while not timer.reached():
            self.screenshot()
            self.device.sleep(interval)

    def run(self) -> None:
        logger.hr('世界任务')
        if not in_open_world(datetime.now()):
            self.schedule_wait_open_world()
            logger.warning('开放世界还没开门（09:00-24:00），本次不执行，已排到开门时间')
            raise TaskEnd(self.TASK_NAME)

        success = False
        bonus_enabled = False
        try:
            self.prepare_game()
            # 加量卡：先启用，接完任务再关掉（关闭放在 finally，失败/阵亡也不会让卡一直烧）
            if self.bonus_multiplier() != BonusMultiplier.NONE:
                bonus_enabled = self.enable_bonus()
            success = self.world_task_flow()
        finally:
            if bonus_enabled:
                self.disable_bonus()
            # 监测是临时的：本次运行结束就收掉（后面做任务的流程会自己再 start）
            self.task_monitor.stop()
            # 正常/异常都要重排，避免 next_run 停在过去霸占调度
            self.schedule_next_run(success=success)
        raise TaskEnd(self.TASK_NAME)

    # ---------------------------------------------------------------- 加量卡
    def bonus_multiplier(self) -> BonusMultiplier:
        """配置里选的加量卡（NONE = 不加量）"""
        return self.config.world_task.world_task_config.bonus_multiplier

    def enable_bonus(self, timeout: float = 40) -> bool:
        """
        进任务加量页，按配置启用加量卡

        页面与按钮语义都复用自动任务的实现（`read_bonus_counts` / `set_bonus_state`）：
        按钮显示「停止加量」= 已激活，显示「激活加量卡」= 未激活；同一时间只允许一张卡生效。

        卡不够要接的任务数（7 张）就跳过：不启用、也不报错，照常不加量接任务。
        :return: 真的启用了返回 True
        """
        want = self.bonus_multiplier()
        if want == BonusMultiplier.NONE:
            return False

        logger.hr(f'启用加量卡 [{want.value}]')
        self.ui_reset_current_page()
        if not self.ui_goto(page_bonus, timeout=timeout):
            raise GameStuckError('Bonus page does not appear')

        counts = self.read_bonus_counts()
        key = '3x' if want == BonusMultiplier.X3 else '5x'
        have = counts.get(key) or 0
        need = len(self.ACCEPT_TASKS)
        logger.attr('加量卡', f'{key} 需要 {need} 张，拥有 {have} 张')
        if have < need:
            logger.warning(f'加量卡不足（{key} 要 {need} 张，只有 {have} 张），跳过加量')
            return False

        self.set_bonus_state(want)
        logger.info(f'加量卡已启用：{want.value}（{have} 张）')
        return True

    def disable_bonus(self, timeout: float = 40) -> bool:
        """
        回任务加量页把加量卡停掉（两张都停）

        接完任务必须关，否则卡会一直生效烧掉。这里失败只告警，不覆盖任务本身的成败。
        """
        logger.hr('关闭加量卡')
        try:
            self.ui_reset_current_page()
            if not self.ui_goto(page_bonus, timeout=timeout):
                logger.error('加量页没打开，加量卡可能还在生效，请手动检查')
                return False
            self.set_bonus_state(BonusMultiplier.NONE)
            logger.info('加量卡已关闭')
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f'关闭加量卡失败：{e}')
            return False

    # ---------------------------------------------------------------- 玩法
    def world_task_flow(self) -> bool:
        """
        当前阶段：接 7 个跨服任务 + 进世界之巅地图 + 做采集类任务

        真机页面链路（2026-11-05 录制）：
        守门人对话 -> 点「查看世界之巅活动」-> 世界之巅活动弹窗（顶部 tab 条）
        -> 世界之巅 tab（活动主页，带黄帝立绘和「进入世界之巅」按钮）-> 跨服任务 tab（接取列表）
        -> 按顺序接 7 个任务 -> 回世界之巅 tab 点「进入世界之巅」-> 等地点变成世界之巅
        -> 任务页里逐个做采集类任务

        **重启上线后人物会自动离开世界之巅回到域外灵岛**，所以：
        - 已经在地图里（上一轮没做完 / 手动进来的）：跳过接取，直接做任务；
        - 不在图里：先走接取阶段，再 `ensure_world_peak_map()` 重新进图。

        :return: 接取阶段正常走完、并且在世界之巅里做完能做的任务返回 True
        """
        if self.map_in_location(self.WORLD_PEAK_LOCATION):
            logger.info('已经在世界之巅地图里，跳过接取阶段，直接做任务')
            # 人可能就在出生点旁边（重跑/复位），试一下判断阵营
            self.camp = self.detect_camp() or getattr(self, 'camp', None)
            return self.run_world_peak_tasks()

        if not self.find_npc(self.NPC_NEW):
            logger.warning('没找到入口 NPC（世界之巅守门人）')
            return False
        logger.info('入口 NPC 已确认：世界之巅守门人')

        if not self.open_activity_panel():
            logger.warning('世界之巅活动弹窗没打开')
            return False
        # 接取只需要「跨服任务」这一个 tab：直接切过去，不再先回世界之巅（省一次切 tab + OCR）。
        # 进世界之巅的阶段再用 ensure_activity_tab(ACTIVITY_HOME_TAB) 归位。
        if not self.switch_tab(self.ACTIVITY_TASK_TAB):
            logger.warning(f'没切到 tab [{self.ACTIVITY_TASK_TAB}]')
            return False
        # 切完拿新一帧再判断（appear 用的是 device.image，不刷新会读到切换前的画面）
        self.screenshot()
        if not self.appear(self.I_ITEM_5):
            logger.warning('跨服任务列表里没看到「接取」按钮')
            return False
        logger.info('跨服任务列表已打开')

        # 接任务：监测从这一刻开始（接取时有的任务会当场判定完成）
        self.task_monitor.start()
        accepted = self.accept_tasks()
        self.accepted_tasks = accepted
        if accepted:
            logger.attr('本次接取', ', '.join(accepted))
        elif self.task_list_readable:
            # 列表能读、但没有一个能接 = 今天已经全接过了（重跑时会走到这里）
            logger.info('跨服任务列表里没有可接的任务（今天应该已经接过了）')
        else:
            logger.warning('一个跨服任务都没接上')

        if self.completed_tasks:
            logger.attr('已完成', ', '.join(self.completed_tasks))
        else:
            logger.info('接完没有当场判定完成的任务')

        # 监测到全部完成就停；否则交给后面的流程继续监测（做任务的流程还没实现）
        if self.all_tasks_completed():
            self.task_monitor.stop()
        else:
            missing = [name for name in self.accepted_tasks
                       if not any(self.task_name_match(text, name) for text in self.completed_tasks)]
            logger.attr('还没完成', ', '.join(missing) or 'none')

        # 接取阶段到此结束；接着确保人在世界之巅地图里（重启上线会自动离开，所以要重新进）
        if not self.ensure_world_peak_map():
            logger.warning('没进到世界之巅地图，把弹窗关掉收尾')
            self.close_activity_panel()
            return False

        # 进图后做任务（当前是三个采集类任务 + 兽毛之争打野兽，其余待补）
        if not self.run_world_peak_tasks():
            logger.warning('世界之巅里的任务没做完')
            return False
        return True

    def run_world_peak_tasks(self) -> bool:
        """
        按 WORLD_PEAK_ORDER 顺序做世界之巅里的任务，已完成（任务页状态「完成」）的跳过

        世界之心没有自己的玩法（打怪掉落），所以排在最后统一判断：
        还没完成就重打「为了荣耀」等掉落，重试 `DROP_RETRY` 次还不掉就今天收工。
        """
        logger.hr('世界之巅：做任务')
        names = [name for name, _kind in self.WORLD_PEAK_ORDER]
        statuses = dict(self.read_task_page_statuses(names))
        pending = [(name, kind) for name, kind in self.WORLD_PEAK_ORDER
                   if statuses.get(name) != 'done']
        logger.attr('要做', ', '.join(name for name, _ in pending) or 'none')
        if not pending:
            logger.info('任务页显示全做完了，跳过')
            return True

        for name, kind in pending:
            if kind == 'drop':
                continue              # 掉落任务最后统一判断
            if not self.run_world_peak_task(name, kind):
                logger.warning(f'任务 [{name}] 没做完，先停')
                return False

        return self.ensure_drop_task()

    def run_world_peak_task(self, name: str, kind: str) -> bool:
        """按玩法分派做单个任务"""
        if kind == 'gather':
            positions = dict(self.GATHER_TASKS)
            return self.run_gather_task(name, positions[name])
        if kind == 'fight':
            rows = {row[0]: row for row in self.FIGHT_TASKS}
            target, position, corner = rows[name][1:]
            return self.run_fight_task(name, target, position, corner)
        if kind == 'glory':
            return self.run_glory_task()
        raise ValueError(f'Unknown task kind [{kind}] for [{name}]')

    def ensure_drop_task(self) -> bool:
        """
        世界之心是打怪掉落：还没完成就重打「为了荣耀」等掉落

        掉落看脸，所以重试 `DROP_RETRY` 次还没掉到就今天收工（不算任务失败，
        否则会因为一个概率掉落让整个任务反复重跑）。
        """
        if self.task_completed(self.DROP_TASK):
            logger.info(f'{self.DROP_TASK} 已经掉了，完成')
            return True

        for attempt in range(1, self.DROP_RETRY + 1):
            logger.info(f'{self.DROP_TASK} 还没完成，重打 {self.GLORY_TASK} 等掉落 '
                        f'（第 {attempt}/{self.DROP_RETRY} 次）')
            if not self.run_glory_task():
                logger.warning(f'重打 {self.GLORY_TASK} 失败')
                break
            if self.task_completed(self.DROP_TASK):
                logger.info(f'{self.DROP_TASK} 掉到了，完成')
                return True

        logger.warning(f'{self.DROP_TASK} 没掉到（掉落看脸），今天先收工')
        return True

    def ensure_world_peak_map(self, timeout: float = 40) -> bool:
        """
        确保人在世界之巅地图里

        **重启游戏上线后人物会自动离开世界之巅回到域外灵岛**，所以每次要做图里的任务之前
        都得确认一次：不在就走「找守门人 -> 打开活动弹窗 -> 进入世界之巅」重新进图。
        """
        if self.map_in_location(self.WORLD_PEAK_LOCATION):
            logger.info('已经在世界之巅地图里')
            return True

        logger.info('不在世界之巅地图（重启上线会自动离开），重新进图')
        if not self.find_npc(self.NPC_NEW):
            logger.warning('找不到入口 NPC（世界之巅守门人），进不了图')
            return False
        if not self.open_activity_panel():
            logger.warning('世界之巅活动弹窗打不开，进不了图')
            return False
        entered = self.enter_world_peak(timeout=timeout)
        if entered:
            # 刚进图就站在阵营出生点旁边，这时候判断阵营最准
            self.camp = self.detect_camp() or getattr(self, 'camp', None)
        return entered

    # ---------------------------------------------------------------- 世界之巅：做任务
    def task_completed(self, name: str) -> bool:
        """监测到的完成提示里有没有这个任务"""
        if not name:
            return False
        return any(self.task_name_match(text, name) for text in self.completed_tasks)

    def open_task_page(self, timeout: float = 30) -> bool:
        """打开任务页（左侧栏「任务」）"""
        logger.hr('打开任务页', level=2)
        self.ui_reset_current_page()
        if not self.ui_goto(page_task, timeout=timeout):
            logger.warning('任务页没打开')
            return False
        return True

    def close_task_page(self, timeout: float = 10) -> bool:
        """关掉任务页（右上角返回）"""
        timer = Timer(timeout).start()
        clicked = False
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_CLOSE_RETURN):
                return True
            if self.appear_then_click(self.I_CLOSE_RETURN, interval=1):
                clicked = True
                self.device_record_clear()
                self.device.sleep(0.5)
            self.device.sleep(0.3)
        logger.warning('任务页关不掉')
        return not self.appear(self.I_CLOSE_RETURN)

    def device_record_clear(self) -> None:
        """点完立刻清记录（避免连点保护）"""
        self.device.click_record_clear()

    def task_page_items(self) -> list:
        """任务页列表 OCR：[(文本, 中心x, 中心y)]"""
        self.screenshot()
        image = self.device.image
        roi = self.O_TASK_PAGE_LIST.roi
        items = []
        for item in self.O_TASK_PAGE_LIST.detect_and_ocr(image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text:
                continue
            box = item.box
            x = int((min(p[0] for p in box) + max(p[0] for p in box)) / 2) + roi[0]
            y = int((min(p[1] for p in box) + max(p[1] for p in box)) / 2) + roi[1]
            items.append((text, x, y))
        return items

    def task_page_rows(self) -> list:
        """
        读任务页列表：[(任务名, 状态, 寻路按钮x, 寻路按钮y)]

        按「寻路」按钮分行；同一行里 x<900 的文字拼起来才是任务名（OCR 会把
        「兽毛之争」拆成两段），含「完成/进行中」的那段是状态。
        """
        items = self.task_page_items()
        anchors = [(x, y) for text, x, y in items if '寻路' in text]
        rows = []
        for walk_x, walk_y in anchors:
            names, status = [], ''
            for text, x, y in items:
                if abs(y - walk_y) > 25:
                    continue
                if '完成' in text or '进行中' in text:
                    status = text
                    continue
                if x >= 900:
                    continue          # 中间那列空着，名字列在 x<900
                if re.fullmatch(r'\[\d+\]', text):
                    continue          # 等级前缀 [66]
                names.append((x, text))
            name = ''.join(text for _x, text in sorted(names))
            rows.append((name, status, walk_x, walk_y))
        return rows

    def find_task_page_row(self, name: str):
        """在任务页里找某任务行，返回 (任务名, 状态, 寻路x, 寻路y)；没有返回 None"""
        for row in self.task_page_rows():
            if name and name in row[0]:
                return row
        return None

    def swipe_task_page_list(self, forward: bool = True, before=None) -> bool:
        """
        滑任务页的列表（任务多的时候列表可以滚动）

        真机实测：**没有惯性** —— 拖多少像素列表就走多少（拖 350px ≈ 4.5 行），
        所以拖拽距离就是滚动量，可以放心用它定位。

        :param forward: True=往下翻（找更靠后的任务）
        :param before: 滑动前的行名序列；调用方已经读过就传进来，省一次 OCR
        :return: 这次滑动后列表内容变了没有（没变就是到底了）
        """
        if before is None:
            before = tuple(row[0] for row in self.task_page_rows())
        x = 700                      # 避开左侧栏和右侧「寻路」按钮列
        if forward:
            start, end = (x, 600), (x, 250)
        else:
            start, end = (x, 250), (x, 600)
        logger.info(f'滑任务页列表 {"下" if forward else "上"} {start} -> {end}')
        self.device.hold_drag(start, end, hold=0.2, control_name='task_page_scroll')
        self.device.click_record_clear()
        self.device.sleep(0.5)
        after = tuple(row[0] for row in self.task_page_rows())
        if before == after:
            logger.info('任务页列表没滑动（到底了）')
            return False
        return True

    def find_task_page_row(self, name: str, max_scroll: int = 3):
        """
        在任务页里找某任务行，返回 (任务名, 状态, 寻路x, 寻路y)；没有返回 None

        任务多时列表可以滚动：当前屏没有就往下翻着找，翻不动（到底）就收手。
        判「到底」用「这一屏的行名和上一屏一样」——比数次数可靠。
        """
        seen = set()
        for attempt in range(max_scroll + 1):
            rows = self.task_page_rows()
            for row in rows:
                if name and name in row[0]:
                    return row
            signature = tuple(row[0] for row in rows)
            if signature in seen:
                break                # 已经到底，再滑也没用
            seen.add(signature)
            if attempt >= max_scroll:
                break
            if not self.swipe_task_page_list(forward=True, before=signature):
                break
        logger.warning(f'任务页里没找到 [{name}]（翻了 {len(seen) - 1} 次）')
        return None

    def task_page_walk_to(self, name: str, row=None) -> bool:
        """在任务页点某任务的「寻路」按钮（row 可以传现成的行，省一次 OCR）"""
        row = row or self.find_task_page_row(name)
        if row is None:
            logger.warning(f'任务页里没找到 [{name}]')
            return False
        row_name, status, walk_x, walk_y = row
        logger.info(f'任务 [{row_name}] 状态[{status}]，点寻路 ({walk_x},{walk_y})')
        self.device.click(x=walk_x, y=walk_y, control_name=f'walk_{name}')
        self.device.click_record_clear()
        return True

    def wait_task_page_closed(self, timeout: float = 6) -> bool:
        """
        等任务页自动关闭（点了「寻路」之后游戏会自己关掉），关不掉再兜底点右上角返回
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_CLOSE_RETURN):
                return True
            self.device.sleep(0.3)
        logger.warning('任务页没有自动关闭，兜底点右上角返回')
        return self.close_task_page()

    def map_wait_arrive_at(self, position: tuple, tolerance: int = None,
                           timeout: float = 120, interval: float = 0.4,
                           still_timeout: float = 3.0) -> bool:
        """
        轮询坐标，等走到指定点附近

        只在**第一次到位**时用：一读到「在目标点容差内」就返回，不要求「站定几秒」——
        自动寻路本来就会停在点上，多等那几秒纯属浪费（真机实测回点位时白等 3 秒+）。

        :param position: 目标坐标 (x, y)
        :param interval: 轮询间隔（秒）
        :param still_timeout: 走了一段又停住、仍离目标很远时，多久放弃（自动寻路失败的兜底）
        :return: 到了返回 True
        """
        tolerance = self.WOOD_POS_TOLERANCE if tolerance is None else tolerance
        logger.hr(f'等走到 {position[0]},{position[1]}', level=2)
        timer = Timer(timeout).start()
        last = None
        still_since = None
        while not timer.reached():
            # 长时间轮询，清卡死记录，避免被框架判成卡死
            self.device.stuck_record_clear()
            location = self.map_move_read_pos()
            if location is None:
                self.device.sleep(interval)
                continue

            x, y = location[1], location[2]
            if self.map_pos_match(x, y, position, tolerance):
                logger.info(f'走到 {x},{y}（目标 {position[0]},{position[1]}）')
                return True

            # 兜底：走了一段又停住、却离目标很远 -> 自动寻路失败，别干等超时
            if last is not None and abs(x - last[1]) <= 2 and abs(y - last[2]) <= 2:
                still_since = still_since or datetime.now()
                if (datetime.now() - still_since).total_seconds() >= still_timeout:
                    logger.warning(f'寻路停在 {x},{y}，离目标 {position} 太远')
                    return False
            else:
                still_since = None
            last = location
            self.device.sleep(interval)
        logger.warning(f'走到 {position} 超时')
        return False

    def gather_until_complete(self, name: str, timeout: float = 600) -> bool:
        """
        循环采集直到任务完成

        玩法（真机确认）：切目标直到锁到一个「采集」动作的目标（**不校验名称**）
        -> 点右下角动作按钮（采集）-> 按钮从「采集」变回「攻击」= 这份采完了
        -> 继续锁下一个，直到聊天栏报「任务完成」。
        """
        logger.hr(f'采集直到 [{name}] 完成', level=2)
        timer = Timer(timeout).start()
        count = 0
        while not timer.reached():
            if self.task_completed(name):
                logger.info(f'[{name}] 已完成，共采集 {count} 次')
                return True

            if not self.ui_ensure_target_locked(action=ACTION_GATHER, max_switch=6):
                logger.warning('附近没有可采集的目标，等一会儿再找')
                self.device.sleep(1.0)
                continue

            x, y = ACTION_BUTTON_DISC_CENTER
            self.device.click(x=x, y=y, control_name='action_gather')
            self.device.click_record_clear()
            count += 1

            # 等按钮从「采集」变回「攻击」（= 这一份采完/目标消失）
            lost = self.ui_target_lost_check(action=ACTION_GATHER)
            wait = Timer(8).start()
            while not wait.reached():
                self.screenshot()
                if self.task_completed(name):
                    logger.info(f'[{name}] 已完成，共采集 {count} 次')
                    return True
                if lost():
                    break
                self.device.sleep(0.3)
        logger.warning(f'采集 [{name}] 超时')
        return False

    def run_gather_task(self, name: str, position: tuple) -> bool:
        """
        做一个采集类任务：任务页寻路 -> 走到完成点 -> 循环采集到完成

        已完成的两道关都跳过：
        - 本次运行里已经看到过「xxx已完成」的提示（`task_completed`）；
        - 任务页里这一行状态已经是「完成」（重跑时靠这个跳过，不白走一趟）。
        """
        logger.hr(f'世界任务：{name}')
        if self.task_completed(name):
            logger.info(f'[{name}] 本次已收到完成提示，跳过')
            return True
        if not self.open_task_page():
            return False

        row = self.find_task_page_row(name)
        if row is None:
            logger.warning(f'任务页里没找到 [{name}]')
            self.close_task_page()
            return False
        if '完成' in row[1]:
            logger.info(f'任务页显示 [{name}] 已完成，跳过')
            self.close_task_page()
            return True

        # 点「寻路」后游戏会自己关掉任务页，不用再点关闭；没关才兜底
        if not self.task_page_walk_to(name, row=row):
            self.close_task_page()
            return False
        if not self.wait_task_page_closed():
            return False

        if not self.map_wait_arrive_at(position):
            logger.warning(f'没走到 [{name}] 的完成点 {position}')
            return False
        return self.gather_until_complete(name)

    # ---------------------------------------------------------------- 打怪类任务
    def fight_click_rule(self, key: str):
        """小地图点位规则：兽毛之争 / 黄金比例 直接用任务名，为了荣耀用 glory_xxx"""
        rules = {
            '兽毛之争': self.C_BEAST_POINT,
            '黄金比例': self.C_GOLDEN_POINT,
            'glory_huangdi': self.C_GLORY_HUANGDI_POINT,
            'glory_yandi': self.C_GLORY_YANDI_POINT,
        }
        if key not in rules:
            raise ValueError(f'Unknown fight point [{key}], expect {list(rules)}')
        return rules[key]

    def read_task_page_statuses(self, names: list) -> list:
        """
        读任务页，返回 [(任务名, 'done' / 'pending' / 'missing')]

        任务多时列表会滚，所以先把整个列表读下来再比；读不到任务页就都按 pending 处理
        （宁可按没做继续，也不要因为读不到就跳过）。
        """
        if not self.open_task_page():
            return [(name, 'pending') for name in names]
        try:
            rows = self.read_all_task_rows()
        finally:
            self.close_task_page()

        result = []
        for name in names:
            status = 'missing'
            for row in rows:
                if self.task_name_match(row[0], name):
                    status = 'done' if '完成' in row[1] else 'pending'
                    break
            result.append((name, status))
        return result

    def read_all_task_rows(self, max_scroll: int = 4) -> list:
        """把任务页整个列表读下来（自动往下翻页收集，同名只留第一次见到的）"""
        collected, seen_names, seen_screens = [], set(), set()
        for attempt in range(max_scroll + 1):
            current = self.task_page_rows()
            for row in current:
                if row[0] in seen_names:
                    continue
                seen_names.add(row[0])
                collected.append(row)
            signature = tuple(row[0] for row in current)
            if signature in seen_screens:
                break
            seen_screens.add(signature)
            if attempt >= max_scroll:
                break
            if not self.swipe_task_page_list(forward=True, before=signature):
                break
        return collected

    def run_fight_task(self, task_name: str, target: str, position: tuple, corner: str,
                       rule_key: str = None, lock_timeout: float = None) -> bool:
        """
        做一个打怪任务：小地图拖到锚点角 -> 点刷新点 -> 走到打架位 -> 循环打目标

        :param task_name: 任务名（用于判断完成提示）
        :param target: 目标怪名字（锁目标时校验）
        :param position: 打架位坐标
        :param corner: 小地图锚点角（bottom_right / top_left ...）
        :param rule_key: 小地图点位规则（默认用任务名）
        :param lock_timeout: 多久锁不到目标就放弃（None = 一直等，打怪任务用）
        """
        logger.hr(f'世界任务：{task_name}')
        if self.task_completed(task_name):
            logger.info(f'[{task_name}] 本次已收到完成提示，跳过')
            return True
        rule_key = rule_key or task_name
        if not self.fight_goto_point(task_name, position, corner, rule_key=rule_key):
            return False
        return self.fight_until_complete(task_name, target, position, corner,
                                          rule_key=rule_key, lock_timeout=lock_timeout)

    def fight_goto_point(self, task_name: str, position: tuple, corner: str,
                         rule_key: str = None) -> bool:
        """
        去打架位：小地图拖到指定角落 -> 点刷新点 -> 主动关小地图 -> 轮询等走到

        只是被打退几步（人还在打架位附近）时不用重开地图，直接接着打。
        """
        logger.hr(f'去 [{task_name}] 的刷新点', level=2)
        if not self.fight_drifted(position):
            logger.info('已经在打架位附近，不用重开地图')
            return True
        if not self.map_open_minimap_tap():
            logger.warning('小地图打不开')
            return False
        if not self.map_pan_to_corner(corner):
            logger.warning(f'小地图没拖到 [{corner}]')
            return False
        rule = self.fight_click_rule(rule_key or task_name)
        x, y = rule.coord()
        logger.info(f'小地图点刷新点 ({x},{y})')
        self.device.click(x=x, y=y, control_name=rule.name)
        self.device.click_record_clear()
        # 主动关小地图，别指望它自己关（盖住画面就没法判断）
        self.close_minimap_if_open()
        if not self.map_wait_arrive_at(position,
                                       tolerance=self.FIGHT_DRIFT_TOLERANCE,
                                       timeout=self.FIGHT_TRAVEL_TIMEOUT):
            logger.warning(f'没走到打架位 {position}')
            return False
        return True

    def fight_drifted(self, position: tuple) -> bool:
        """现在是不是已经离开打架位了（坐标读不出来按「没跑偏」处理）"""
        location = self.map_move_read_pos()
        if location is None:
            return False
        return not self.map_pos_match(location[1], location[2], position,
                                      self.FIGHT_DRIFT_TOLERANCE)

    def fight_stop_reason(self, task_name: str, position: tuple, image=None):
        """
        打怪循环的停止条件（每轮都被调用，所以查坐标要按 5 秒节流）

        - 任务完成提示出现 -> 'complete'
        - 每 5 秒查一次坐标，偏离打架位 -> 'drift'（外层回点位后接着打）
        """
        if self.task_completed(task_name):
            logger.info(f'[{task_name}] 已完成')
            return 'complete'
        if self.fight_drift_timer.reached():
            self.fight_drift_timer.reset()
            if self.fight_drifted(position):
                logger.warning(f'人物偏离打架位 {position}，回点位')
                return 'drift'
        return None

    def fight_until_complete(self, task_name: str, target: str,
                             position: tuple, corner: str,
                             rule_key: str = None, lock_timeout: float = None) -> bool:
        """
        打目标怪直到任务完成

        真机玩法（兽毛之争）：刷新点附近 3 只怪、打死 4~5 秒重新刷新，
        所以目标死了不用补锁、直接重新锁下一只；技能按任务「战斗配置」里的预设放。

        :param lock_timeout: 多久一直锁不到目标就放弃（None = 一定要打到达成/超时）
        :return: 任务完成返回 True
        """
        logger.hr(f'打 [{target}]', level=2)
        rule_key = rule_key or task_name
        preset = self.battle_preset()
        slots = self.battle_plan_slots(preset=preset)
        if not slots:
            logger.warning('没识别到可释放的技能位，只点普通攻击')

        self.fight_drift_timer = Timer(self.FIGHT_DRIFT_CHECK).start()
        timer = Timer(self.FIGHT_TIMEOUT).start()
        lock_timer = None
        while not timer.reached():
            if self.task_completed(task_name):
                logger.info(f'[{task_name}] 已完成')
                return True

            if not self.ui_ensure_target_locked(target_name=target):
                if lock_timeout is None:
                    logger.info(f'没锁到 [{target}]，等刷新')
                    self.device.sleep(1.0)
                    continue
                # 阵营未知时用来「探一探这个点位对不对」：锁不到就换另一个
                lock_timer = lock_timer or Timer(lock_timeout).start()
                if lock_timer.reached():
                    logger.warning(f'{lock_timeout}s 都没锁到 [{target}]，换另一个点位')
                    return False
                self.device.sleep(1.0)
                continue
            lock_timer = None

            reason = self.battle_skill_loop(
                slots,
                check_stop=lambda image=None: self.fight_stop_reason(task_name, position),
                target_check=lambda image: self.ui_target_attackable(image=image),
                include_attack=self.battle_use_normal_attack(),
            )
            if self.task_completed(task_name):
                logger.info(f'[{task_name}] 已完成')
                return True
            if reason == 'drift':
                if not self.fight_goto_point(task_name, position, corner, rule_key=rule_key):
                    return False
            # 其它情况（target_lost = 这只打死了、timeout = 这轮没打出去）都回到外层重新锁
        logger.warning(f'打 [{task_name}] 超时')
        return False

    # ---------------------------------------------------------------- 为了荣耀（分阵营）
    def detect_camp(self):
        """
        按当前位置判断自己是哪个阵营（只在刚进图的出生点附近才准）

        世界之巅每天首次进图随机分配阵营：黄帝出生点 ≈ (56,60)、炎帝 ≈ (439,384)。
        """
        location = self.map_move_read_pos()
        if location is None:
            return None
        for camp, spawn in self.CAMP_SPAWNS.items():
            if self.map_pos_match(location[1], location[2], spawn, self.CAMP_SPAWN_TOLERANCE):
                logger.attr('阵营', f'{camp}（出生点附近 {location[1]},{location[2]}）')
                return camp
        logger.info(f'当前位置 {location[1]},{location[2]} 不在任何阵营出生点附近，判断不出阵营')
        return None

    def run_glory_task(self) -> bool:
        """
        为了荣耀：和打怪任务同一套流程，只是按阵营换「目标 / 打架位 / 小地图点位」

        阵营未知（比如中途重跑、人早就不在出生点了）时两个点位都试一遍：
        每个点位给 `GLORY_PROBE_LOCK_TIMEOUT` 秒，锁不到目标就换另一个。
        """
        logger.hr(f'世界任务：{self.GLORY_TASK}')
        if self.task_completed(self.GLORY_TASK):
            logger.info('本次已收到完成提示，跳过')
            return True

        camp = getattr(self, 'camp', None) or self.detect_camp()
        if camp is not None:
            return self.run_glory_variant(camp)

        logger.warning('不知道自己属于哪个阵营，两个点位都试一遍')
        for candidate in self.GLORY_VARIANTS:
            if self.run_glory_variant(candidate, lock_timeout=self.GLORY_PROBE_LOCK_TIMEOUT):
                self.camp = candidate
                return True
        logger.warning('两个阵营的点位都没打成')
        return False

    def run_glory_variant(self, camp: str, lock_timeout: float = None) -> bool:
        """按阵营打对应的守门人"""
        target, position, corner, rule_key = self.GLORY_VARIANTS[camp]
        logger.info(f'为了荣耀：{camp} 阵营 -> 目标 [{target}] 打架位 {position}（{corner}）')
        return self.run_fight_task(self.GLORY_TASK, target, position, corner,
                                   rule_key=rule_key, lock_timeout=lock_timeout)

    # ---------------------------------------------------------------- 进入世界之巅
    def enter_world_peak(self, timeout: float = 40) -> bool:
        """
        在世界之巅 tab 里点「进入世界之巅」-> 等右上角地点变成「世界之巅」

        :return: 已经在该地图里返回 True
        """
        logger.hr('进入世界之巅')
        if self.map_in_location(self.WORLD_PEAK_LOCATION):
            logger.info('已经在世界之巅地图里')
            return True
        if not self.ensure_activity_tab(self.ACTIVITY_HOME_TAB):
            logger.warning(f'没回到 [{self.ACTIVITY_HOME_TAB}] tab')
            return False

        for attempt in (1, 2):
            x, y = self.C_ENTER_WORLD_PEAK.coord()
            logger.info(f'Click 进入世界之巅 at ({x},{y}) (attempt {attempt})')
            self.device.click(x=x, y=y, control_name=self.C_ENTER_WORLD_PEAK.name)
            self.device.click_record_clear()

            # 进图要过读条，期间地点读不出来是正常的，所以别让卡死检测误报
            timer = Timer(timeout).start()
            while not timer.reached():
                self.device.stuck_record_clear()
                if self.map_in_location(self.WORLD_PEAK_LOCATION):
                    logger.info('已进入世界之巅地图')
                    self.ui_reset_current_page()
                    return True
                self.device.sleep(0.5)
            logger.warning(f'第 {attempt} 次点「进入世界之巅」没进图')
        logger.warning('进入世界之巅失败')
        return False

    # ---------------------------------------------------------------- 世界之巅活动弹窗
    def _ocr_items(self) -> list:
        """顶部 tab 条 OCR 原始结果（单测里替换这一层）"""
        roi = self.O_NEW.roi
        items = []
        for item in self.O_NEW.detect_and_ocr(self.device.image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text:
                continue
            box = item.box
            x = int((min(p[0] for p in box) + max(p[0] for p in box)) / 2) + roi[0]
            y = int((min(p[1] for p in box) + max(p[1] for p in box)) / 2) + roi[1]
            items.append((text, x, y))
        items.sort(key=lambda i: i[1])
        # 记下位置：同一套 tab 布局下再切 tab 就不用重新 OCR 了
        positions = getattr(self, '_tab_positions', None)
        if positions is not None:
            for text, x, y in items:
                index = self.tab_index(text)
                if index >= 0:
                    positions[self.ACTIVITY_TABS[index]] = (x, y)
        return items

    def close_activity_panel(self, timeout: float = 8) -> bool:
        """
        关掉世界之巅活动弹窗（右上角绿 X）

        轮询只用便宜特征（黄帝立绘 / 接取按钮 / 守门人对话的选项行），都不在就当关掉了；
        只有都不在但 tab OCR 仍认为弹窗开着（停在别的 tab）时才继续点 X。
        """
        timer = Timer(timeout).start()
        need_ocr = True
        while not timer.reached():
            self.screenshot()
            cheap = (self.appear(self.I_ITEM_4) or self.appear(self.I_ITEM_5)
                     or self.appear(self.I_ITEM_3))
            if not cheap:
                if not need_ocr or not self.activity_panel_open(refresh=False):
                    return True
                need_ocr = False
            if self.appear_then_click(self.I_CLOSE_X_GREEN, interval=1):
                self.device.click_record_clear()
            self.device.sleep(0.5)
        logger.warning('Activity panel does not close')
        return False

    # ---------------------------------------------------------------- 接取任务
    def bonus_promo_open(self, refresh: bool = True) -> bool:
        """
        「当前任务加量功能未激活」推广弹窗开着没有

        :param refresh: 是否先截一帧。循环里刚截过图就传 False，省掉 300ms 的截图节流等待。
        """
        if refresh:
            self.maybe_screenshot()
        image = getattr(self.device, 'image', None)
        if image is None:
            return False
        # 便宜的特征（弹窗右上角绿X，ROI 只给了弹窗那一块）优先
        if self.I_PROMO_CLOSE.match(image):
            return True
        text = self.O_BONUS_PROMO.ocr_single_line(image)
        return '加量' in (text or '')

    def close_bonus_promo(self, timeout: float = 10) -> bool:
        """
        关掉加量推广弹窗（点绿X，不勾「今日不再提示」）

        与 AutoTask.click_auto_start 里处理加量提示的做法一致：出现就点它的绿X关掉。
        注意不能勾「今日不再提示」—— 那是改玩家的游戏设置。
        :return: 已经关掉（或本来就没弹）返回 True
        """
        if not self.bonus_promo_open():
            return True
        logger.info('加量推广弹窗出现，点绿X关掉')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.bonus_promo_open(refresh=False):
                logger.info('Bonus promo closed')
                return True
            if self.appear_then_click(self.I_PROMO_CLOSE, interval=1):
                self.device.click_record_clear()
            # 0.5s 一帧会在 1.5s 的完成提示窗口里只采到 3 帧，这里用 0.3s
            self.device.sleep(0.3)
        logger.warning('Bonus promo does not close')
        return False

    @staticmethod
    def _strip_task_prefix(text: str) -> str:
        """去掉列表里的「【跨服】」「[跨服]」「跨服-周六限时」之类前缀"""
        text = re.sub(r'[\[【]?\s*跨服[^\]】]*[\]】]?', '', text or '')
        return text.strip()

    def task_name_match(self, ocr_text: str, name: str) -> bool:
        """
        列表里的任务名匹配

        两处都不能省：
        1. 先剥掉「【跨服】」前缀 —— 不去掉的话 `ocr_name_match` 的「前两字一致」规则
           永远对不上（"【跨" vs "世界"）；
        2. 只在 ACCEPT_TASKS 这 7 个已知任务名里挑最接近的来比 —— 「世界之巅/世界之心/
           世界印记」前三个字一样，直接算相似度 世界之巅 vs 世界之心 = 0.75，会互相认错。
        """
        text = self._strip_task_prefix(ocr_text)
        if not text or not name:
            return False
        if name in text or text in name:
            return True

        best, best_score = None, 0.0
        for candidate in self.ACCEPT_TASKS:
            score = difflib.SequenceMatcher(None, text, candidate).ratio()
            if score > best_score:
                best, best_score = candidate, score
        if best != name or best_score < 0.7:
            return False
        return True

    def task_rows(self) -> list:
        """
        读跨服任务列表：[(任务名, 接取按钮中心x, 接取按钮中心y)]

        接取按钮用 I_ITEM_5 在按钮列里全匹配出来，任务名按每行的 y 单行识别（rec-only）。
        实测：整块 detect_and_ocr 688ms，逐行 rec-only 34ms/行，5 行约 170ms。
        同一帧只读一次（按帧缓存），重复调用不重复 OCR。
        """
        self.screenshot()
        image = self.device.image
        key = id(image) if image is not None else None
        if key is not None and self._rows_cache[0] == key:
            return list(self._rows_cache[1])

        buttons = sorted(self.I_ITEM_5.match_all_any(image, threshold=self.I_ITEM_5.threshold),
                         key=lambda m: m[2])
        rows = []
        for _score, x, y, w, h in buttons:
            center_y = y + h // 2
            crop = image[max(0, center_y - 22): center_y + 22, 100:1000]
            text, _confidence = self._ocr.ocr_single_line(crop)
            rows.append((text.strip(), x + w // 2, center_y))

        if key is not None:
            self._rows_cache = (key, rows)
        return list(rows)

    def wait_task_accepted(self, name: str, timeout: float = 8) -> bool:
        """
        接取之后确认真的接上了

        游戏会把接过/完成的任务移到列表末尾，所以「可见列表里没有它了」就算接上；
        期间还要顺手关掉可能又弹出来的加量推广弹窗。
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.close_bonus_promo()
            rows = self.task_rows()
            if rows and not any(self.task_name_match(text, name) for text, _, _ in rows):
                return True
            self.device.sleep(0.3)
        return False

    def accept_tasks(self, timeout: float = 30) -> list:
        """
        按顺序接 ACCEPT_TASKS 里的 7 个跨服任务

        接过的任务会被移到列表末尾，所以正常情况下每次都是接第一行；
        第一行对不上时再在可见行里按名字找（加 OCR 兜底，避免列表滚动后点错）。
        :return: 成功接取的任务名（按接取顺序）
        """
        logger.hr('接取跨服任务')
        want = self.bonus_multiplier()
        if want != BonusMultiplier.NONE:
            logger.attr('加量卡', f'{want.value}（run() 里已按需启用，接完会关掉）')

        accepted = []
        self.task_list_readable = False
        for expected in self.ACCEPT_TASKS:
            rows = self.task_rows()
            if not rows:
                logger.warning('跨服任务列表读不出来，停止接取')
                break
            self.task_list_readable = True

            name, bx, by = rows[0]
            if not self.task_name_match(name, expected):
                # 兜底：第一行不是预期的，就在可见行里按名字找
                found = next((row for row in rows if self.task_name_match(row[0], expected)), None)
                if found is None:
                    logger.warning(f'列表里没有 [{expected}]（可能今天已接过），跳过')
                    continue
                name, bx, by = found

            logger.info(f'接取 [{name}] at ({bx},{by})')
            self.device.click(x=bx, y=by, control_name=f'accept_{expected}')
            self.device.click_record_clear()
            # 不再固定 sleep：wait_task_accepted 里每次轮询都会截图，1.5s 的完成提示照样采得到
            self.close_bonus_promo()

            if self.wait_task_accepted(name):
                accepted.append(expected)
                logger.info(f'已接取 [{expected}]')
            else:
                logger.warning(f'接取 [{expected}] 后列表没变化，可能没点上')
        logger.attr('本次接取', ', '.join(accepted) or 'none')
        return accepted

    def tab_items(self) -> list:
        """当前可见的 tab 文字与可点中心 [(文本, x, y)]，按 x 排序（同一帧只 OCR 一次）"""
        self.maybe_screenshot()
        image = getattr(self.device, 'image', None)
        key = id(image) if image is not None else None
        if key is not None and self._tabs_cache[0] == key:
            return list(self._tabs_cache[1])
        items = self._ocr_items()
        if key is not None:
            self._tabs_cache = (key, items)
        return list(items)

    def find_tab(self, name: str):
        """在当前 tab 条里找 name，返回可点中心 (x, y)；不在视野里返回 None"""
        cached = getattr(self, '_tab_positions', {}).get(name)
        if cached is not None:
            return cached
        for text, x, y in self.tab_items():
            if self.ocr_name_match(text, name):
                return x, y
        return None

    def swipe_tabs(self, forward: bool = True) -> bool:
        """
        滑 tab 条：forward=True 往右翻（露出右边的 tab），False 往左翻回来

        :return: 这次滑动后已知 tab 的布局有没有变化；没变化说明已经到底
        """
        before = self.tab_signature()
        x1, y1, x2, y2 = self.S_NEW.coord()
        if not forward:
            x1, y1, x2, y2 = x2, y2, x1, y1
        logger.info(f'Swipe activity tabs {"right" if forward else "left"} '
                    f'({x1},{y1}) -> ({x2},{y2})')
        self.device.swipe(p1=(x1, y1), p2=(x2, y2), control_name=self.S_NEW.name)
        self.device.click_record_clear()
        self.device.sleep(0.6)
        # 滑过之后 tab 位置全变了，缓存作废
        if getattr(self, '_tab_positions', None) is not None:
            self._tab_positions.clear()
        after = self.tab_signature()
        if before == after:
            logger.info('Tab bar did not scroll')
            return False
        return True

    def tab_signature(self) -> tuple:
        """已知 tab 的 (文本, x)，用来判断 tab 条到底了没（OCR 噪声项不算）"""
        return tuple((text, x) for text, x, _ in self.tab_items()
                     if any(self.ocr_name_match(text, tab) for tab in self.ACTIVITY_TABS))

    def click_tab(self, name: str, coord=None) -> bool:
        """点 tab：切过去之后等画面稳定"""
        coord = coord or self.find_tab(name)
        if not coord:
            return False
        logger.info(f'Click activity tab [{name}] at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name=f'tab_{name}')
        self.device.click_record_clear()
        # 实测切 tab 0.4s 内就刷新完了，原来等 1s 纯属浪费
        self.device.sleep(0.5)
        # 切完立刻刷新一帧，否则调用方拿到的还是切换前那帧（appear 不会自己截图）
        self.screenshot()
        return True

    def tab_index(self, name: str) -> int:
        """tab 在 ACTIVITY_TABS 里的序号；认不出来返回 -1"""
        for index, tab in enumerate(self.ACTIVITY_TABS):
            if self.ocr_name_match(name, tab):
                return index
        return -1

    def visible_tabs(self) -> list:
        """当前可见的已知 tab：[(文本, x, y, index)]，按 x 排序"""
        items = []
        for text, x, y in self.tab_items():
            index = self.tab_index(text)
            if index >= 0:
                items.append((text, x, y, index))
        return items

    def tab_direction(self, name: str):
        """
        目标 tab 在该往哪边翻

        tab 条顺序固定（世界之巅在最左），拿「目标序号」和「当前可见 tab 的序号范围」比：
        目标在可见范围左边就往左翻，在右边就往右翻。

        世界之巅最容易踩坑：弹窗停在后面的 tab（比如上次看的跨服任务又往右滑过）时，
        它不在视野里，这时**必须往左翻**，继续往右翻永远翻不到。
        :return: True=往右翻 / False=往左翻 / None=认不出现状（按往左翻回去处理）
        """
        target = self.tab_index(name)
        visible = self.visible_tabs()
        if target < 0 or not visible:
            return None
        first = min(index for _, _, _, index in visible)
        last = max(index for _, _, _, index in visible)
        if target < first:
            return False
        if target > last:
            return True
        return True

    def switch_tab(self, name: str, max_swipe: int = 3) -> bool:
        """
        切到指定 tab：视野里直接点；不在就按 tab 顺序判断方向翻过去，翻不动了换方向

        :return: 点到了返回 True（是否真的切过去由调用方用该 tab 的特征确认）
        """
        logger.hr(f'Switch activity tab [{name}]')
        for _ in range(max_swipe + 1):
            coord = self.find_tab(name)
            if coord:
                return self.click_tab(name, coord)
            forward = self.tab_direction(name)
            if forward is None:
                # 认不出当前停在哪个 tab：先往左翻（世界之巅在最左，总能先回到第一页）
                forward = False
            if not self.swipe_tabs(forward=forward):
                logger.info(f'Tab bar cannot scroll {"right" if forward else "left"}, '
                            f'try the other way')
                if not self.swipe_tabs(forward=not forward):
                    logger.info('Tab bar does not scroll at all')
                    break
        logger.warning(f'Activity tab [{name}] not found')
        return False

    def ensure_activity_tab(self, name: str = None, max_swipe: int = 3) -> bool:
        """
        确保停在某个 tab 页面（默认世界之巅：活动主页）

        弹窗会记住上次停在哪个 tab，打开时不一定是世界之巅，所以进弹窗后先归位。
        :return: 已经在该 tab（或切过去了）返回 True
        """
        name = name or self.ACTIVITY_HOME_TAB
        if not self.switch_tab(name, max_swipe=max_swipe):
            return False
        # 切过去之后用该 tab 的内容特征确认（世界之巅 = 黄帝立绘）
        if self.tab_index(name) == 0:
            timer = Timer(6).start()
            while not timer.reached():
                self.screenshot()
                if self.appear(self.I_ITEM_4):
                    logger.info(f'Activity tab [{name}] is showing')
                    return True
                if self.find_tab(name) is None and self.activity_panel_open():
                    break
                self.device.sleep(0.3)
            logger.warning(f'Activity tab [{name}] content does not appear')
            return False
        return True

    def activity_panel_open(self, refresh: bool = True) -> bool:
        """
        世界之巅活动弹窗开着没有

        先用便宜的特征（世界之巅 tab 的黄帝立绘 / 跨服任务 tab 的接取按钮，各 4ms），
        都不到才去 OCR tab 条（那一步 700ms+），因为弹窗停在荣誉商店这类 tab 时没有上面两个特征。
        """
        if refresh:
            self.maybe_screenshot()
        if self.appear(self.I_ITEM_4) or self.appear(self.I_ITEM_5):
            return True
        # 切过 tab 之后立绘就没了，改看顶部 tab 条：认出 2 个以上已知 tab 就算开着
        known = [text for text, _, _ in self.tab_items()
                 if any(self.ocr_name_match(text, tab) for tab in self.ACTIVITY_TABS)]
        return len(known) >= 2

    def open_activity_panel(self, timeout: float = 12) -> bool:
        """
        在守门人对话里点「查看世界之巅活动」-> 等世界之巅活动弹窗

        真机实测：点这个选项行弹窗就直接出来了（不需要再点「确定」）。
        :return: 弹窗打开返回 True
        """
        logger.hr('打开世界之巅活动弹窗')
        if self.activity_panel_open():
            logger.info('Activity panel is already open')
            self._tab_positions.clear()
            return True

        timer = Timer(timeout).start()
        clicked = False
        while not timer.reached():
            self.screenshot()
            # 轮询只查两个便宜特征（各 4ms）：黄帝立绘 / 接取按钮。
            # tab 条 OCR 要 900ms，放在每轮里会把开弹窗拖成 4s+
            if self.appear(self.I_ITEM_4) or self.appear(self.I_ITEM_5):
                logger.info('Activity panel opened')
                self._tab_positions.clear()
                return True
            if not clicked and self.appear_then_click(self.I_ITEM_3, interval=1):
                logger.info('Click 查看世界之巅活动')
                clicked = True
                self.device.click_record_clear()
                continue
            self.device.sleep(0.3)
        # 兜底：可能停在别的 tab（既没有立绘也没有接取按钮），最后用 tab OCR 确认一次
        if self.activity_panel_open():
            logger.info('Activity panel opened (no cheap feature, tab OCR confirmed)')
            self._tab_positions.clear()
            return True
        logger.warning('Activity panel does not open')
        return False

    # ---------------------------------------------------------------- NPC 识别
    def npc_click(self, npc: str):
        """NPC 名称 -> 小地图上的点位（录制的点击规则）"""
        clicks = {
            self.NPC_NEW: self.C_NEW,
            self.NPC_ITEM_2: self.C_ITEM_2,
        }
        if npc not in clicks:
            raise ValueError(f'Unknown npc [{npc}], expect {list(clicks)}')
        return clicks[npc]

    def npc_template(self, npc: str):
        """NPC 名称 -> 对话框特征（模板匹配）"""
        templates = {
            self.NPC_NEW: self.I_NEW,
            self.NPC_ITEM_2: self.I_ITEM_2,
        }
        if npc not in templates:
            raise ValueError(f'Unknown npc [{npc}], expect {list(templates)}')
        return templates[npc]

    def npc_dialog(self):
        """
        当前屏幕上是哪个 NPC 的对话框

        :return: NPC 名称；都不是返回 None
        """
        self.maybe_screenshot()
        for npc in (self.NPC_NEW, self.NPC_ITEM_2):
            if self.appear(self.npc_template(npc)):
                return npc
        return None

    def minimap_top_right(self) -> bool:
        """打开小地图并把视口拖到右上角（NPC 点位就是按这个视口录的）"""
        logger.hr('小地图 -> 右上角')
        if not self.map_open_minimap_tap():
            logger.warning('小地图打不开')
            return False
        return self.map_pan_to_corner('top_right')

    def close_minimap_if_open(self, timeout: float = 3) -> bool:
        """
        主动关小地图（点完点位不指望它自己关）

        小地图开着会盖住屏幕中间，对话框弹出来了也看不见，只能白等到超时。
        :return: 关掉（或本来就没开）返回 True
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_MAP_MINIMAP_WORLD_MAP):
                return True
            logger.info('Close minimap')
            self.map_close_minimap()
            self.device.sleep(0.3)
        logger.warning('Minimap does not close')
        return False

    def open_npc_dialog(self, npc: str, retry: int = None) -> bool:
        """
        开小地图到右上角 -> 点录像里的 NPC 点位 -> 主动关小地图 -> 等反应

        「有没有反应」看两件事：对话框出来没有、主页面右上角的人物坐标动没动。
        点不中（地图没拖到位、点在遮罩上）时坐标不会动，这种情况直接重试，
        不要傻等对话框超时。
        :return: 弹出的对话框是本 NPC 返回 True
        """
        retry = self.NPC_RETRY if retry is None else retry
        click = self.npc_click(npc)
        for attempt in range(1, retry + 1):
            # 点之前先记一次坐标（主页面右上角）：点完拿它判断人物动没动
            before = self.map_current_location()
            if not self.minimap_top_right():
                logger.warning(f'Minimap pan failed, attempt {attempt}/{retry}')
                continue

            x, y = click.coord()
            logger.info(f'Click npc [{npc}] on minimap at ({x},{y}) '
                        f'(attempt {attempt}/{retry})')
            self.device.click(x=x, y=y, control_name=click.name)
            self.device.click_record_clear()
            # 必做：主动关小地图，不然对话框弹出来也被盖住
            self.close_minimap_if_open()

            if self.wait_npc_response(npc, before):
                return True
            logger.warning(f'NPC [{npc}] no response, attempt {attempt}/{retry} failed')
        return False

    def _location_moved(self, before, after) -> bool:
        """比较两次读到的地图坐标（三点：地点名 / x / y）"""
        if not before or not after:
            return False
        try:
            return (abs(int(after[1]) - int(before[1])) > self.NPC_MOVE_TOLERANCE
                    or abs(int(after[2]) - int(before[2])) > self.NPC_MOVE_TOLERANCE)
        except (TypeError, ValueError, IndexError):
            return False

    @staticmethod
    def _location_text(location) -> str:
        if not location or len(location) < 3:
            return 'none'
        return f'{location[0]} {location[1]},{location[2]}'

    def wait_npc_response(self, npc: str, before, timeout: float = None) -> bool:
        """
        点完 NPC 之后的判定

        1. 对话框出现且是本 NPC -> 成功（有时点完立刻弹，人物不动）；
        2. 坐标在变 -> 人物在走过去，继续等对话框（最多 NPC_DIALOG_TIMEOUT）；
        3. 坐标一直不动、对话框也没有 -> 这一下没点中，交给上层重试；
        4. 走着走着坐标停住还不出对话框 -> 卡住了，同样重试。
        """
        timeout = self.NPC_DIALOG_TIMEOUT if timeout is None else timeout
        timer = Timer(timeout).start()
        move_timer = Timer(self.NPC_MOVE_TIMEOUT).start()
        stuck_timer = Timer(self.NPC_STUCK_TIMEOUT)
        moved = False
        last = before
        while not timer.reached():
            self.screenshot()
            opened = self.npc_dialog()
            if opened == npc:
                logger.info(f'NPC dialog [{npc}] appeared')
                return True
            if opened is not None:
                logger.warning(f'Expect dialog [{npc}] but got [{opened}]')
                return False

            location = self.map_current_location(image=self.device.image)
            if location is not None:
                if self._location_moved(last, location):
                    if not moved:
                        logger.attr('NPC move',
                                    f'{self._location_text(before)} -> {self._location_text(location)}')
                    moved = True
                    last = location
                    stuck_timer.reset()
                elif moved and stuck_timer.reached():
                    logger.warning(f'NPC [{npc}]: 坐标停在 {self._location_text(location)} '
                                   f'不动、对话框也不出，按卡住重试')
                    return False

            if not moved and move_timer.reached():
                logger.warning(f'NPC [{npc}]: 点完 {self.NPC_MOVE_TIMEOUT}s 坐标没变化，'
                               f'按「没点中」重试（before={self._location_text(before)}）')
                return False
            self.device.sleep(0.5)

        logger.warning(f'NPC dialog [{npc}] does not appear in {timeout}s')
        return False

    def close_npc_dialog(self, timeout: float = 6) -> bool:
        """关掉 NPC 对话框（右上角绿 X），关掉返回 True"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.npc_dialog() is None:
                return True
            if self.appear_then_click(self.I_CLOSE_X_GREEN, interval=1):
                self.device.click_record_clear()
            self.device.sleep(0.4)
        logger.warning('NPC dialog does not close')
        return False

    # ---------------------------------------------------------------- 地图与找 NPC
    def read_location(self, retry: int = 3) -> tuple:
        """
        读当前地点（右上角名字 + 坐标）

        OCR 偶尔会只认出坐标（真机 2026-11-05：读到 "J,29"），这种时候不能当成
        「不在目标地图」—— 按乱码去传送既找不到目标点，还会白开一次世界地图列表。
        """
        for attempt in range(retry):
            location = self.map_current_location()
            if location is not None:
                return location
            if attempt + 1 < retry:
                self.device.sleep(0.5)
        return None

    def ensure_location(self, name: str = None) -> bool:
        """
        判断当前所在地图：不在 NPC 所在地图就传送过去

        :param name: 目标地图，默认取配置 world_task_config.location
        :return: 已经在目标地图（或传送成功）返回 True
        """
        if name is None:
            name = self.config.world_task.world_task_config.location
        location = self.read_location()
        if location is None:
            # 读不出来：按「大概率已经在」处理，继续往下走（找 NPC 失败会重试并暴露问题），
            # 不按乱码去传送
            logger.warning('读不出当前地点，跳过传送判断')
            return True
        if self.map_name_match(location[0], name):
            logger.attr('Current location', f'{location[0]} {location[1]},{location[2]}')
            return True
        logger.info(f'Not at [{name}] (now {location}), teleport')
        if not self.map_teleport(name):
            logger.warning(f'Teleport to [{name}] failed')
            return False
        return True

    def find_npc(self, npc: str, retry: int = 2) -> bool:
        """
        判断地图 -> 打开小地图拖到右上角 -> 点 NPC -> 关小地图 -> 确认对话框是这个 NPC

        :param npc: NPC 名称（NPC_NEW / NPC_ITEM_2）
        :param retry: 整个流程最多试几轮（外层；单轮里 open_npc_dialog 自己还会重试点位）
        :return: 该 NPC 的对话框已经打开返回 True
        """
        logger.hr(f'Find npc [{npc}]')
        for attempt in range(1, retry + 1):
            if not self.ensure_location():
                return False
            opened = self.npc_dialog()
            if opened == npc:
                logger.info(f'NPC dialog [{npc}] is already open')
                return True
            if opened is not None:
                logger.info(f'Another npc dialog [{opened}] is open, close it')
                self.close_npc_dialog()
            if self.open_npc_dialog(npc):
                return True
            logger.warning(f'Find npc [{npc}] failed, attempt {attempt}/{retry}')
        return False

    # ---------------------------------------------------------------- 准备
    def prepare_game(self) -> None:
        """确保游戏在线并停留在主页面"""
        logger.hr('准备游戏（在线 + 主页面）')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            logger.info('游戏已登录到主页面')
            return
        try:
            from tasks.GameUi.pages.page import page_main
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=60):
                raise GameStuckError('Main page does not appear')
            logger.info('游戏在线且在主页面')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError):
            logger.warning('无法切回主页面，重启游戏')
            self.ui_restart_game()

    # ---------------------------------------------------------------- 排期
    def schedule_next_run(self, success: bool = True) -> None:
        """下次 = 现在 + 成功/失败间隔，再夹进开放世界时段"""
        scheduler = self.config.model.world_task.scheduler
        interval = scheduler.success_interval if success else scheduler.failure_interval
        next_run = next_run_after(datetime.now(), interval,
                                  float_seconds=self.float_seconds(scheduler))
        self.apply_next_run(next_run)
        logger.attr('WorldTask 排期', f'{next_run:%Y-%m-%d %H:%M:%S} (success={success})')

    def schedule_wait_open_world(self) -> None:
        """窗口没开：排到最近一次开门（当天 09:00，已经过了就次日 09:00）"""
        scheduler = self.config.model.world_task.scheduler
        next_run = clamp_to_open_world(datetime.now(),
                                       float_seconds=self.float_seconds(scheduler))
        self.apply_next_run(next_run)
        logger.attr('WorldTask 排期', f'{next_run:%Y-%m-%d %H:%M:%S} (wait open world)')

    def apply_next_run(self, next_run: datetime) -> None:
        """写入下次运行时间：时间已经算好，不再走默认的服务器刷新逻辑（server=False）"""
        self.set_next_run(task=self.TASK_NAME, target=next_run, success=None,
                          finish=True, server=False)

    @staticmethod
    def float_seconds(scheduler) -> int:
        """排期的浮动时间（秒）：被夹到 09:00 整时随机后移，避免所有任务同一秒启动"""
        float_time = getattr(scheduler, 'float_time', None)
        if float_time is None:
            return 0
        return float_time.hour * 3600 + float_time.minute * 60 + float_time.second


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
