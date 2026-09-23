# This Python file uses the following encoding: utf-8
"""
上古狩猎（AncientHunt）：

开放时间（默认 12:35-12:45、17:05-17:15，配置 open_times），每天参与一次即可：

1. 提前 advance_time（默认 3 分钟）把游戏准备到主页面（不在线则重启登录，有遮挡弹窗先关掉）
2. 到开放时间后：
   - 传送到牧野的传送落点（map_ensure_location_initial，落点见 MAP_INITIAL_POS）
   - 收起右上角菜单
   - 点击「目标」按钮锁定「上古狩猎神」，读到目标名匹配才算锁定成功
   - 点击攻击键弹出交互框，点击「进入上古狩猎场」
   - 等待 enter_wait 秒后重启游戏（ui_restart_game），重启成功即任务完成
3. 参与成功后跳过当天剩下的时间段，排到第二天的准备时间；失败则排到当天下一个时间段

今天是否已经参与过：先查活动-活跃页的「上古狩猎」完成度（参与过就不再进）；
流程失败会保留到下一个时间段重试。

活动开放提醒弹窗（popup_hunt_notice）由 GlobalGame 的全局弹窗处理自动关闭。
"""
from datetime import datetime, timedelta

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.AncientHunt.assets import AncientHuntAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main


class ScriptTask(GameUi, AncientHuntAssets):

    # 调度优先级名（ConfigManual.SCHEDULER_PRIORITY，用于长时间等待时给高优先级任务让路）
    SCHEDULER_NAME = 'AncientHunt'

    # 读取目标名的放大倍数与置信度下限（真机实测 2x/3x 最好）
    target_name_scales = (2, 3)
    target_name_score = 0.5

    def run(self) -> None:
        logger.hr('Ancient hunt')
        cfg = self.config.ancient_hunt.ancient_hunt_config

        window = self.current_window()
        if window is None:
            logger.info('Not in ancient hunt open time, skip')
            self.schedule_next_window()
            raise TaskEnd('AncientHunt')
        start, end = window
        logger.attr('Ancient hunt window', f'{start:%H:%M}-{end:%H:%M}')

        # 提前准备：游戏在线且在主页面（关闭遮挡弹窗）
        self.prepare_main_page()

        # 今天已经参与过就不再进（开放两个时间段，参与一次即可）
        if self.hunt_completed():
            logger.info('Ancient hunt already done today, skip')
            self.schedule_next_window(after_done=True)
            raise TaskEnd('AncientHunt')

        # 查活跃页会打开活动弹窗，回到主页面再等开放时间
        self.prepare_main_page()

        # 还没到开放时间就先等（提前准备时间之内）
        self.wait_until_open(start)

        if not self.enter_hunt_flow():
            logger.error('Ancient hunt failed')
            self.schedule_next_window()
            raise TaskEnd('AncientHunt')

        # 进入狩猎场后等一会儿再重启游戏
        self.device.sleep(cfg.enter_wait)
        logger.info('Restart game after entering the hunt')
        self.ui_restart_game()

        self.schedule_next_window(after_done=True)
        raise TaskEnd('AncientHunt')

    # ---------------------------------------------------------------- 时间
    @staticmethod
    def parse_clock(text: str):
        """解析 HH:MM 为 datetime.time"""
        try:
            return datetime.strptime(text.strip(), '%H:%M').time()
        except ValueError:
            logger.warning(f'Invalid time in open_times: {text!r}')
            return None

    def iter_windows(self, days: int = 2) -> list:
        """今天/明天（days 天）的开放时间段 [(start, end), ...]"""
        cfg = self.config.ancient_hunt.ancient_hunt_config
        windows = []
        today = datetime.now().date()
        for offset in range(days):
            date = today + timedelta(days=offset)
            for part in cfg.open_times.split(','):
                part = part.strip()
                if not part or '-' not in part:
                    continue
                left, right = part.split('-', 1)
                start_time, end_time = self.parse_clock(left), self.parse_clock(right)
                if start_time is None or end_time is None:
                    continue
                windows.append((datetime.combine(date, start_time), datetime.combine(date, end_time)))
        return windows

    def current_window(self):
        """
        当前所处的开放时间段（含提前准备时间），不在任何时间段内返回 None
        """
        now = datetime.now()
        for start, end in self.iter_windows(days=2):
            if start - self.advance <= now <= end:
                return start, end
        return None

    @property
    def advance(self) -> timedelta:
        """提前准备的时间"""
        advance_time = self.config.ancient_hunt.ancient_hunt_config.advance_time
        return timedelta(hours=advance_time.hour,
                         minutes=advance_time.minute,
                         seconds=advance_time.second)

    def schedule_next_window(self, after_done: bool = False) -> None:
        """
        排到下一个开放时间段的准备时间（start - advance）
        :param after_done: 今天已经参与过，跳过当天剩下的时间段
        """
        now = datetime.now()
        target = None
        for start, _ in self.iter_windows(days=2):
            prepare = start - self.advance
            if prepare <= now:
                continue
            if after_done and start.date() == now.date():
                continue
            target = prepare
            break

        if target is None:
            logger.warning('Ancient hunt schedule is empty')
            self.set_next_run(task='AncientHunt', success=True, finish=True, server=False)
            return
        logger.attr('Next ancient hunt prepare', target)
        self.set_next_run(task='AncientHunt', target=target, success=None, finish=True, server=False)

    def wait_until_open(self, start: datetime) -> None:
        """
        等到开放时间（等待期间保持画面不动，只清空卡死记录）
        """
        while 1:
            remain = (start - datetime.now()).total_seconds()
            if remain <= 0:
                break
            logger.attr('Ancient hunt', f'距开放时间 {start:%H:%M:%S} 还有 {int(remain)}s')
            self.device.sleep(min(5, max(1, remain)))
            self.reset_records()
        logger.info(f'Ancient hunt open time reached: {start:%H:%M:%S}')

    # ---------------------------------------------------------------- 准备
    def hunt_completed(self) -> bool:
        """
        查活动-活跃页，「上古狩猎」今天是否已经完成（查不到按未完成处理）
        """
        try:
            completed = self.active_task_completed(self.config.ancient_hunt.ancient_hunt_config.active_task_name)
        except (GameStuckError, GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'Unable to check ancient hunt state ({e}), treat as not completed')
            return False
        logger.attr('Ancient hunt completed', completed)
        return completed

    def prepare_main_page(self) -> None:
        """
        确保游戏在线且停留在主页面：地图导航要从主页面上的小地图入口开始，
        不在主页面（登录页/其他页面）先回主页面，回不去则重启游戏，
        再关掉会盖住小地图入口的活动弹窗
        """
        logger.hr('Prepare main page')
        if not self.device.app_is_running():
            logger.info('Game is not running, restart game')
            self.ui_restart_game()
            return

        self.ui_reset_current_page()
        try:
            if self.ui_goto(page_main, timeout=30):
                # ui_goto 只看主页面右下角特征，活动弹窗开着也算到达，这里再关掉遮挡弹窗
                self.map_close_main_popup()
                logger.info('Game is on the main page')
                return
            logger.warning('Failed to go back to the main page, restart game')
        except (GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'Main page is not reachable ({e}), restart game')
        self.ui_restart_game()

    # ---------------------------------------------------------------- 狩猎流程
    def enter_hunt_flow(self) -> bool:
        """
        传送到牧野 -> 收起菜单 -> 锁定上古狩猎神 -> 攻击 -> 点击进入上古狩猎场
        """
        logger.hr('Enter ancient hunt')
        cfg = self.config.ancient_hunt.ancient_hunt_config

        if not self.map_ensure_location_initial(cfg.target_location):
            logger.error(f'Unable to reach [{cfg.target_location}] initial position')
            return False

        # 收起右上角菜单，避免遮挡目标按钮/攻击按钮
        self.ui_close_menu()

        if not self.lock_target():
            return False
        if not self.attack_target():
            return False
        return self.click_enter_hunt()

    def lock_target(self, timeout: int = None) -> bool:
        """
        点击「目标」按钮锁定目标，读到目标名并匹配才算成功
        """
        logger.hr('Lock target')
        cfg = self.config.ancient_hunt.ancient_hunt_config
        timeout = timeout or cfg.lock_timeout
        timer = Timer(timeout).start()
        last_name = ''

        while not timer.reached():
            self.reset_records()
            self.screenshot()
            last_name = self.get_target_name()
            if last_name and self.target_name_match(last_name):
                logger.info(f'Target locked: {last_name}')
                return True
            if self.appear_then_click(self.I_TARGET_BUTTON,
                                      action=self.C_TARGET_BUTTON_CLICK,
                                      interval=1):
                logger.info('Click target button')
                continue
            self.device.sleep(0.5)

        logger.warning(f'Target [{cfg.target_name}] not locked, last target: {last_name}')
        return False

    def get_target_name(self) -> str:
        """
        读取当前锁定目标的名称（彩色描边字，统一走 GameUi.ocr_color_name）
        """
        return self.ocr_color_name(self.O_TARGET_NAME,
                                   scales=self.target_name_scales,
                                   min_score=self.target_name_score)

    def target_name_match(self, ocr_text: str) -> bool:
        """
        目标名匹配：包含，或相似度达标（容忍 OCR 形近字误识，如 狩猎 -> 守猎）
        """
        name = self.config.ancient_hunt.ancient_hunt_config.target_name
        if not ocr_text or not name:
            return False
        if name in ocr_text:
            return True
        window = ocr_text[:len(name)]
        return self.ocr_name_match(window, name)

    def attack_target(self, timeout: int = 15) -> bool:
        """
        点击攻击键，等待弹出交互框（识别到「进入上古狩猎场」按钮）
        """
        logger.hr('Attack target')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_ENTER_HUNT):
                logger.info('Enter hunt dialog appeared')
                return True
            x, y = self.C_ATTACK.coord()
            logger.info(f'Click attack at ({x}, {y})')
            self.device.click(x=x, y=y, control_name=self.C_ATTACK.name)
            self.device.click_record_clear()
            self.device.sleep(1)

        logger.warning('Enter hunt dialog does not appear')
        return False

    def click_enter_hunt(self) -> bool:
        """
        点击交互框里的「进入上古狩猎场」
        """
        logger.hr('Enter the hunt field')
        self.screenshot()
        if not self.appear(self.I_ENTER_HUNT):
            logger.warning('Enter hunt button not found')
            return False
        x, y = self.I_ENTER_HUNT.coord()
        logger.info(f'Click enter hunt at ({x}, {y})')
        self.device.click(x=x, y=y, control_name=self.I_ENTER_HUNT.name)
        self.device.click_record_clear()
        return True


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
