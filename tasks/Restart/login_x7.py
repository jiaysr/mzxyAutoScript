# This Python file uses the following encoding: utf-8
from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets as GA
from tasks.Restart.restart_base import RestartBase


class RestartX7(RestartBase):
    """
    小七版登录流程

    开屏登录页 -> 选择服务器 -> 角色列表 -> 角色确认 -> 游戏主界面 -> 关闭弹窗
    所有状态放在同一个循环里判断，不逐步等待页面消失，
    卡死交给系统默认的 stuck_record 超时检测

    每次点击都用最新一帧确认页面特征，点击后等该页面切走再判断下一个状态：
    登录页到服务器页的切换只要 0.5s 左右，用过渡前的旧画面算出来的坐标点击，
    点击会落在已经切过去的新页面上（登录按钮的中心正好压在服务器列表上，于是点错区服）
    """

    # 点击页面按钮后，等待该页面特征消失（页面已经切走）的最长时间（秒）
    page_switch_timeout: int = 8

    def _app_handle_login(self) -> bool:
        logger.hr('App login (x7)')
        self.device.stuck_record_add('LOGIN_CHECK')

        while 1:
            self.screenshot()

            # 主界面：命中主页面特征即登录成功（与 page_main 共用同一素材，
            # 登录期间会有公告栏等弹窗，主页面特征在右下角不受遮挡）
            if self.appear(GA.I_PAGE_MAIN):
                logger.info('Main page appear')
                self.handle_popup()
                logger.info('Login to main confirm')
                return True

            # 登录页：勾选用户协议后点击「登录游戏」
            if self.appear(self.I_X7_LOGIN_ENTER):
                if not self.appear(self.I_X7_LOGIN_AGREE):
                    logger.info('User agreement not checked, check it')
                    self.click_agree_box()
                    continue
                if self.page_click(self.I_X7_LOGIN_ENTER):
                    logger.info('Click login game')
                    continue

            # 选择服务器页：进入上次登录的区服
            elif self.page_click(self.I_X7_SERVER_PAGE, action=self.C_X7_SERVER_LAST_LOGIN):
                logger.info('Click last login server')
                continue

            # 角色列表页：进入上次登录的角色
            elif self.page_click(self.I_X7_CHARACTER_PAGE, action=self.C_X7_CHARACTER_FIRST):
                logger.info('Click character')
                continue

            # 角色确认页：点击「登录角色」
            elif self.page_click(self.I_X7_CHARACTER_CONFIRM, action=self.C_X7_CHARACTER_LOGIN):
                logger.info('Click login character')
                continue

    def click_agree_box(self, interval: float = 1) -> bool:
        """
        勾选用户协议：同样用最新一帧确认「还在登录页且没有勾选」再点击
        """
        self.screenshot()
        if not self.appear(self.I_X7_LOGIN_ENTER) or self.appear(self.I_X7_LOGIN_AGREE):
            return False
        return self.click(self.C_X7_LOGIN_AGREE_CLICK, interval=interval)

    def page_click(self, page, action=None, interval: float = 1) -> bool:
        """
        页面按钮点击：用最新一帧确认页面特征 -> 点击 -> 等该页面特征消失

        :param page: 页面特征（同时作为页面已经切走的判断依据）
        :param action: 点击目标，默认点击 page 匹配到的位置
        :param interval: 同一个按钮两次点击之间的最小间隔（秒）
        :return: 是否执行了点击且页面已经切走
        """
        action = action if action is not None else page
        timer = self.interval_timer.get(action.name)
        if timer is None:
            timer = self.interval_timer[action.name] = Timer(interval)
        if not timer.reached():
            return False

        # 点击前重新截一帧：截图到点击之间有 0.2s 左右，页面可能已经切走
        self.screenshot()
        if not self.appear(page):
            return False
        if isinstance(action, RuleImage) and action is not page and not self.appear(action):
            return False

        x, y = action.coord()
        logger.info(f'Click {action.name} at ({x}, {y}) @ {page.name}')
        self.device.click(x=x, y=y, control_name=action.name)
        self.device.click_record_clear()
        timer.reset()
        # 等页面切走再返回，过渡期间不会在同一个位置上重复点击
        return self.wait_page_leave(page)

    def wait_page_leave(self, page, timeout: int = None) -> bool:
        """
        等待页面特征消失（页面已经切走）
        """
        timeout = timeout or self.page_switch_timeout
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if not self.appear(page):
                return True
            if timer.reached():
                logger.warning(f'{page.name} still appears after {timeout}s, retry later')
                return False
            self.device.sleep(0.2)

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
