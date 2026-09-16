# This Python file uses the following encoding: utf-8
from datetime import datetime, time

from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from tasks.Restart.restart_base import RestartBase
from tasks.base_task import Time

LOGIN_PENDING = 'x7 login flow is not implemented yet, capture game screenshots and mark assets first'


class RestartX7(RestartBase):
    """
    小七版登录流程
    """

    def _app_handle_login(self) -> bool:
        logger.hr('App login (x7)')
        self.device.stuck_record_add('LOGIN_CHECK')

        self.wait_until_game_foreground()
        self.handle_splash()
        self.handle_login()
        self.handle_notice()
        self.wait_until_main_page()

        logger.info('Login to main confirm')
        return True

    def wait_until_game_foreground(self, timeout: int = 60) -> bool:
        """
        等待游戏进程回到前台
        """
        timer = Timer(timeout).start()
        while 1:
            self.device.screenshot()
            if self.device.app_is_running():
                logger.info('Game is in foreground')
                return True
            if timer.reached():
                raise GameStuckError('Game is not in foreground')
            self.device.sleep(0.5)

    def handle_splash(self) -> bool:
        """
        小七版开屏、SDK 健康公告，待标注资产后实现
        """
        raise RequestHumanTakeover(LOGIN_PENDING)

    def handle_login(self) -> bool:
        """
        小七版账号登录页，待标注资产后实现
        """
        raise RequestHumanTakeover(LOGIN_PENDING)

    def handle_notice(self) -> bool:
        """
        小七版公告、更新、活动弹窗，待标注资产后实现
        """
        raise RequestHumanTakeover(LOGIN_PENDING)

    def wait_until_main_page(self) -> bool:
        """
        等待进入游戏主界面，待标注资产后实现
        """
        raise RequestHumanTakeover(LOGIN_PENDING)

    def after_login(self) -> None:
        """
        登录后的收尾：领取奖励与定时安排
        """
        if self.config.restart.harvest_config.enable_ap:
            now = datetime.now()
            if now.time() < time(12, 0):
                self.custom_next_run(task='Restart', custom_time=Time(12, 0), time_delta=0)
            elif now.time() < time(20, 0):
                self.custom_next_run(task='Restart', custom_time=Time(20, 0), time_delta=0)
            else:
                self.custom_next_run(task='Restart', custom_time=Time(12, 0), time_delta=1)
