# This Python file uses the following encoding: utf-8
from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.Restart.restart_base import RestartBase


class RestartX7(RestartBase):
    """
    小七版登录流程

    开屏登录页 -> 选择服务器 -> 角色列表 -> 角色确认 -> 游戏主界面 -> 关闭弹窗
    """

    def _app_handle_login(self) -> bool:
        logger.hr('App login (x7)')
        self.device.stuck_record_add('LOGIN_CHECK')

        self.wait_until_game_foreground()
        self.handle_start_page()
        self.handle_server_page()
        self.handle_character_page()
        self.handle_character_confirm_page()
        self.wait_until_main_page()
        self.handle_popup()

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

    def wait_until_rule_disappear(self, rule, name: str, timeout: int = 30) -> bool:
        logger.info(f'Wait until {name} disappear')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            self.device.sleep(0.2)
            if not self.appear(rule):
                logger.info(f'{name} disappear')
                return True
            if timer.reached():
                raise GameStuckError(f'{name} still appears')

    def handle_start_page(self, timeout: int = 30) -> bool:
        """
        开屏登录页：勾选用户协议后点击「登录游戏」
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            self.device.sleep(0.2)
            if not self.appear(self.I_X7_LOGIN_ENTER):
                if timer.reached():
                    raise GameStuckError('Login page does not appear')
                continue

            logger.info('Login page appear')
            if not self.appear(self.I_X7_LOGIN_AGREE):
                logger.info('User agreement not checked, check it')
                self.click(self.C_X7_LOGIN_AGREE_CLICK, interval=1)
                continue

            if self.appear_then_click(self.I_X7_LOGIN_ENTER, interval=1):
                logger.info('Click login game')
                self.wait_until_rule_disappear(self.I_X7_LOGIN_ENTER, 'login page')
                return True

    def handle_server_page(self, timeout: int = 30) -> bool:
        """
        选择服务器页：进入上次登录的区服
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            self.device.sleep(0.2)
            if not self.appear(self.I_X7_SERVER_PAGE):
                if timer.reached():
                    raise GameStuckError('Server page does not appear')
                continue

            logger.info('Server page appear')
            if self.appear_then_click(self.I_X7_SERVER_PAGE,
                                      action=self.C_X7_SERVER_LAST_LOGIN,
                                      interval=1):
                logger.info('Click last login server')
                self.wait_until_rule_disappear(self.I_X7_SERVER_PAGE, 'server page')
                return True

    def handle_character_page(self, timeout: int = 30) -> bool:
        """
        角色列表页：进入上次登录的角色
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            self.device.sleep(0.2)
            if not self.appear(self.I_X7_CHARACTER_PAGE):
                if timer.reached():
                    raise GameStuckError('Character page does not appear')
                continue

            logger.info('Character page appear')
            if self.appear_then_click(self.I_X7_CHARACTER_PAGE,
                                      action=self.C_X7_CHARACTER_FIRST,
                                      interval=1):
                logger.info('Click character')
                self.wait_until_rule_disappear(self.I_X7_CHARACTER_PAGE, 'character page')
                return True

    def handle_character_confirm_page(self, timeout: int = 30) -> bool:
        """
        角色确认页：点击「登录角色」
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            self.device.sleep(0.2)
            if not self.appear(self.I_X7_CHARACTER_CONFIRM):
                if timer.reached():
                    raise GameStuckError('Character confirm page does not appear')
                continue

            logger.info('Character confirm page appear')
            if self.appear_then_click(self.I_X7_CHARACTER_CONFIRM,
                                      action=self.C_X7_CHARACTER_LOGIN,
                                      interval=1):
                logger.info('Click login character')
                self.wait_until_rule_disappear(self.I_X7_CHARACTER_CONFIRM, 'character confirm page')
                return True

    def wait_until_main_page(self, timeout: int = 60) -> bool:
        """
        等待进入游戏主界面：识别主界面底部区域文字是否为「聊天」
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            self.device.sleep(0.2)
            if self.appear(self.O_CHAT):
                logger.info('Main page appear')
                return True
            if timer.reached():
                raise GameStuckError('Main page does not appear')

    def handle_popup(self, timeout: int = 2) -> bool:
        """
        关闭进入游戏后的所有弹窗
        关闭按钮在右半屏内搜索（弹窗位置不固定，如公告栏的叉在偏左处），
        匹配到后点击匹配到的位置，而不是固定坐标
        """
        closes = [self.I_X7_POPUP_CLOSE]
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            closed = False
            for close in closes:
                if self.appear_then_click(close, interval=0.8):
                    logger.info(f'Close popup by {close.name}')
                    timer.reset()
                    closed = True
                    break
            if closed:
                continue
            if timer.reached():
                logger.info('No more popup')
                return True
