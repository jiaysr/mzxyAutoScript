# This Python file uses the following encoding: utf-8
"""
世界任务（WorldTask）：每天一次，只在开放世界时段（09:00-24:00）里跑

当前进度：**认人 + 打开世界之巅活动弹窗** —— 判断是否在 NPC 所在地图（默认域外灵岛）→
打开小地图把视口拖到右上角 → 点录好的 NPC 点位 → **主动关掉小地图** → 用「人物坐标有没有变化」
判断这一下点没点中（没动且没对话框就重试）→ 对话框出来后用模板确认是哪个 NPC
→ 在守门人对话里点「查看世界之巅活动」→ 活动弹窗打开 → 切到「跨服任务」tab → 确认接取列表可见。
接任务 / 进世界之巅 / 做任务 / 交任务后面接着补。

游戏里的页面链路（2026-11-05 录制，见 tasks/WorldTask/res/）：
    守门人对话（I_NEW）→「查看世界之巅活动」选项行（I_ITEM_3）→ 世界之巅活动弹窗
    →「世界之巅」tab 有黄帝立绘（I_ITEM_4）→「跨服任务」tab（O_NEW 读 tab 名，S_NEW 滑 tab 条）
    → 任务列表里每行一个「接取」按钮（I_ITEM_5）
    活动时间：每周星期一到星期五 9:00-24:00（与 schedule.py 的窗口一致）

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
from datetime import datetime

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.WorldTask.assets import WorldTaskAssets
from tasks.WorldTask.schedule import clamp_to_open_world, in_open_world, next_run_after


class ScriptTask(GameUi, WorldTaskAssets):
    SCHEDULER_NAME = 'WorldTask'
    TASK_NAME = 'WorldTask'

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

    def run(self) -> None:
        logger.hr('世界任务')
        if not in_open_world(datetime.now()):
            self.schedule_wait_open_world()
            logger.warning('开放世界还没开门（09:00-24:00），本次不执行，已排到开门时间')
            raise TaskEnd(self.TASK_NAME)

        success = False
        try:
            self.prepare_game()
            success = self.world_task_flow()
        finally:
            # 正常/异常都要重排，避免 next_run 停在过去霸占调度
            self.schedule_next_run(success=success)
        raise TaskEnd(self.TASK_NAME)

    # ---------------------------------------------------------------- 玩法
    def world_task_flow(self) -> bool:
        """
        当前阶段：认人 + 打开世界之巅活动弹窗 + 切到「跨服任务」列表

        真机页面链路（2026-11-05 录制）：
        守门人对话 -> 点「查看世界之巅活动」-> 世界之巅活动弹窗（顶部 tab 条）
        -> 世界之巅 tab（活动主页，带黄帝立绘和「进入世界之巅」按钮）-> 跨服任务 tab（接取列表）

        :return: 走到「跨服任务列表可见」返回 True
        """
        if not self.find_npc(self.NPC_NEW):
            logger.warning('没找到入口 NPC（世界之巅守门人）')
            return False
        logger.info('入口 NPC 已确认：世界之巅守门人')

        if not self.open_activity_panel():
            logger.warning('世界之巅活动弹窗没打开')
            return False
        # 弹窗会记住上次停在哪个 tab，先回到活动主页（世界之巅在最左，不在视野就左翻）
        if not self.ensure_activity_tab(self.ACTIVITY_HOME_TAB):
            logger.warning(f'没回到 [{self.ACTIVITY_HOME_TAB}] tab')
            return False
        if not self.switch_tab(self.ACTIVITY_TASK_TAB):
            logger.warning(f'没切到 tab [{self.ACTIVITY_TASK_TAB}]')
            return False
        if not self.appear(self.I_ITEM_5):
            logger.warning('跨服任务列表里没看到「接取」按钮')
            return False
        logger.info('跨服任务列表已打开（接取/做任务/交付待补）')
        # 目前到此为止：把界面收干净，别把游戏留在弹窗里
        self.close_activity_panel()
        return True

    # ---------------------------------------------------------------- 世界之巅活动弹窗
    def _ocr_items(self) -> list:
        """顶部 tab 条 OCR 原始结果（单测里替换这一层）"""
        self.maybe_screenshot()
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
        return items

    def close_activity_panel(self, timeout: float = 8) -> bool:
        """关掉世界之巅活动弹窗（右上角绿 X）"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.activity_panel_open() and self.npc_dialog() is None:
                return True
            if self.appear_then_click(self.I_CLOSE_X_GREEN, interval=1):
                self.device.click_record_clear()
            self.device.sleep(0.5)
        logger.warning('Activity panel does not close')
        return False

    def tab_items(self) -> list:
        """当前可见的 tab 文字与可点中心 [(文本, x, y)]，按 x 排序"""
        return self._ocr_items()

    def find_tab(self, name: str):
        """在当前 tab 条里找 name，返回可点中心 (x, y)；不在视野里返回 None"""
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
        self.device.sleep(0.8)
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
        self.device.sleep(1.0)
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

    def activity_panel_open(self) -> bool:
        """世界之巅活动弹窗开着没有"""
        self.maybe_screenshot()
        # 停在「世界之巅」tab 时有黄帝立绘
        if self.appear(self.I_ITEM_4):
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
            return True

        timer = Timer(timeout).start()
        clicked = False
        while not timer.reached():
            self.screenshot()
            if self.activity_panel_open():
                logger.info('Activity panel opened')
                return True
            if not clicked and self.appear_then_click(self.I_ITEM_3, interval=1):
                logger.info('Click 查看世界之巅活动')
                clicked = True
                self.device.click_record_clear()
                continue
            self.device.sleep(0.5)
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
    def ensure_location(self, name: str = None) -> bool:
        """
        判断当前所在地图：不在 NPC 所在地图就传送过去

        :param name: 目标地图，默认取配置 world_task_config.location
        :return: 已经在目标地图（或传送成功）返回 True
        """
        if name is None:
            name = self.config.world_task.world_task_config.location
        location = self.map_current_location()
        if location is not None and self.map_name_match(location[0], name):
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
