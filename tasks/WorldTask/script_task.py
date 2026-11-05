# This Python file uses the following encoding: utf-8
"""
世界任务（WorldTask）：每天一次，只在开放世界时段（09:00-24:00）里跑

当前进度：**识别人** —— 判断是否在 NPC 所在地图（默认域外灵岛）→ 打开小地图把视口拖到
右上角 → 点录好的 NPC 点位 → **主动关掉小地图** → 用「人物坐标有没有变化」判断这一下点没点中
（没动且没对话框就重试）→ 对话框出来后用模板确认是哪个 NPC。
接任务 / 进世界之巅 / 交任务的流程后面接着补。

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
        当前阶段：认人。确认能在地图上找到并点开入口 NPC 的对话框。

        :return: 找到入口 NPC 返回 True
        """
        if not self.find_npc(self.NPC_NEW):
            logger.warning('没找到入口 NPC（世界之巅守门人）')
            return False
        logger.info('入口 NPC 已确认：世界之巅守门人')
        return True

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
