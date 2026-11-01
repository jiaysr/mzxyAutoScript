# This Python file uses the following encoding: utf-8
import re

from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets as GA
from tasks.Restart.login_x7 import RestartX7


class RestartOfficial(RestartX7):
    def screenshot(self):
        try:
            self.device.screenshot()
            if self.I_OFFICIAL_ACCOUNT_PAGE.match(self.device.image):
                # 原生密码框显示明文，必须在全局处理可能抛错之前清理截图。
                self.device.screenshot_deque.clear()
                self.device.image[80:262, :] = 0
        except Exception:
            self.device.screenshot_deque.clear()
            raise
        self.handle_death()
        self.handle_captcha()
        self.handle_global_popup()
        return self.device.image

    def load_login_target(self) -> None:
        cfg = self.config.restart.login_official_config
        raw_server = (cfg.server or '').strip()
        match = re.search(r'(\d+)', raw_server)
        if raw_server and not match:
            self.login_failed(f'区服配置无法解析: {raw_server}（应填写如 88区）')
        self.server_zone = int(match.group(1)) if match else None
        self.account_name = cfg.account
        self.character_name = (cfg.character or '').strip()
        logger.attr('登录目标', f'官方版 区服={raw_server or "上次登录"} '
                              f'角色={self.character_name or "左侧默认"}')

    def _app_handle_login(self, timer: Timer = None) -> bool:
        logger.hr('App login (official)')
        self.device.stuck_record_add('LOGIN_CHECK')
        self.load_login_target()
        while True:
            if timer is not None and timer.reached():
                raise GameStuckError(f'官方版登录超过 {timer.limit}s 仍未进入游戏')
            self.screenshot()
            if self.appear(self.I_OFFICIAL_ACCOUNT_PAGE):
                self.check_account_error()
                self.handle_account_page()
                continue
            if self.appear(GA.I_PAGE_MAIN):
                self.handle_popup()
                return True
            if self.appear(self.I_OFFICIAL_LOGIN_ENTER):
                self.handle_login_page()
                continue
            if self.appear(self.I_X7_SERVER_PAGE):
                self.handle_server_page()
                continue
            if self.appear(self.I_X7_CHARACTER_PAGE):
                self.handle_character_page()

    def handle_login_page(self) -> None:
        if not self.appear(self.I_X7_LOGIN_AGREE):
            self.screenshot()
            if self.appear(self.I_OFFICIAL_LOGIN_ENTER) and not self.appear(self.I_X7_LOGIN_AGREE):
                self.click(self.C_X7_LOGIN_AGREE_CLICK, interval=1)
            return
        self.page_click(self.I_OFFICIAL_LOGIN_ENTER)

    def select_from_mine(self) -> bool:
        self.screenshot()
        if not self.appear(self.I_OFFICIAL_SERVER_ACCOUNT_LABEL):
            return super().select_from_mine()
        for _ in range(6):
            self.screenshot()
            entries = self.parse_server_entries(self.O_OFFICIAL_SERVER_LAST_LOGIN)
            if entries:
                last = entries[0]
                if self.server_zone is None or last.zone == self.server_zone:
                    return self.click_server_entry(last)
                break
            self.device.sleep(0.4)
        for _ in range(6):
            entries = self.mine_entries()
            if entries:
                return self.click_target_server(entries)
            self.device.sleep(0.4)
        return False

    def mine_entries(self):
        self.screenshot()
        rule = (self.O_OFFICIAL_SERVER_LIST if self.appear(self.I_OFFICIAL_SERVER_ACCOUNT_LABEL)
                else self.O_X7_SERVER_MINE_LIST)
        return self.parse_server_entries(rule)

    def all_entries(self):
        self.screenshot()
        rule = (self.O_OFFICIAL_SERVER_LIST if self.appear(self.I_OFFICIAL_SERVER_ACCOUNT_LABEL)
                else self.O_X7_SERVER_ALL_LIST)
        return self.parse_server_entries(rule)

    def check_account_error(self) -> None:
        if self.appear(self.I_OFFICIAL_ACCOUNT_ERROR):
            raise RequestHumanTakeover('账号密码错误')

    def handle_account_page(self) -> None:
        self.check_account_error()
        cfg = self.config.restart.login_official_config
        try:
            fields = self.device.u2(className='android.widget.EditText', packageName=self.device.package)
            if fields.count != 2:
                raise RequestHumanTakeover('官方版账号密码输入框未就绪，请检查登录页面')
            if cfg.account:
                fields[0].set_text(cfg.account, timeout=1)
            if cfg.password:
                fields[1].set_text(cfg.password, timeout=1)
            # 使用原生按钮，避免软键盘或错误提示改变按钮坐标。
            self.device.u2(text='确定', className='android.widget.Button',
                           packageName=self.device.package).click(timeout=1)
        except RequestHumanTakeover:
            raise
        except Exception:
            # RPC 异常可能携带 setText 的原始参数。
            raise RequestHumanTakeover('官方版账号输入失败，请检查设备连接或输入框状态') from None
        self.device.click_record_clear()
        timer = Timer(self.ACCOUNT_SCAN_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            self.check_account_error()
            if not self.appear(self.I_OFFICIAL_ACCOUNT_PAGE):
                return
            self.device.sleep(0.2)
        raise GameStuckError('官方版账号提交后未进入角色页面')
