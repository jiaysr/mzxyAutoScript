# This Python file uses the following encoding: utf-8
from module.base.timer import Timer
from module.logger import logger
from tasks.Restart.restart_base import RestartBase


class RestartX7(RestartBase):
    """
    小七版登录流程

    开屏登录页 -> 选择服务器 -> 角色列表 -> 角色确认 -> 游戏主界面 -> 关闭弹窗
    所有状态放在同一个循环里判断，不逐步等待页面消失，
    卡死交给系统默认的 stuck_record 超时检测
    """

    def _app_handle_login(self) -> bool:
        logger.hr('App login (x7)')
        self.device.stuck_record_add('LOGIN_CHECK')

        while 1:
            self.screenshot()

            # 主界面：识别到「聊天」即登录成功
            if self.ocr_appear(self.O_CHAT):
                logger.info('Main page appear')
                self.handle_popup()
                logger.info('Login to main confirm')
                return True

            # 登录页：勾选用户协议后点击「登录游戏」
            if self.appear(self.I_X7_LOGIN_ENTER):
                if not self.appear(self.I_X7_LOGIN_AGREE):
                    logger.info('User agreement not checked, check it')
                    self.click(self.C_X7_LOGIN_AGREE_CLICK, interval=1)
                    continue
                if self.appear_then_click(self.I_X7_LOGIN_ENTER, interval=1):
                    logger.info('Click login game')
                    continue

            # 选择服务器页：进入上次登录的区服
            elif self.appear_then_click(self.I_X7_SERVER_PAGE,
                                        action=self.C_X7_SERVER_LAST_LOGIN,
                                        interval=1):
                logger.info('Click last login server')
                continue

            # 角色列表页：进入上次登录的角色
            elif self.appear_then_click(self.I_X7_CHARACTER_PAGE,
                                        action=self.C_X7_CHARACTER_FIRST,
                                        interval=1):
                logger.info('Click character')
                continue

            # 角色确认页：点击「登录角色」
            elif self.appear_then_click(self.I_X7_CHARACTER_CONFIRM,
                                        action=self.C_X7_CHARACTER_LOGIN,
                                        interval=1):
                logger.info('Click login character')
                continue

    def handle_popup(self, timeout: int = 3) -> bool:
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
