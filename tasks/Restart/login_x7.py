# This Python file uses the following encoding: utf-8
import difflib
import math
import re
from collections import namedtuple

from module.atom.image import RuleImage
from module.base.timer import Timer
from module.exception import GameStuckError, TaskFailed
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets as GA
from tasks.Restart.restart_base import RestartBase

ServerEntry = namedtuple('ServerEntry', 'zone x y text')
AccountEntry = namedtuple('AccountEntry', 'index rule raw name')
CharacterEntry = namedtuple('CharacterEntry', 'index text login_rule')


class RestartX7(RestartBase):
    """
    小七版登录流程

    开屏登录页 -> 选择服务器 -> 账号选择 -> 角色选择 -> 游戏主界面 -> 关闭弹窗

    外层是单循环状态机（每帧识别当前页面），各选择步骤是状态内的独立子流程：
    - 区服：先看「我的服务器」的上次登录区服和已有角色服务器列表；
      没有则切「全部服务器」，按 93->92->... 的降序列表查找，必要时用 S_X7_SERVER_SCROLL_NEXT 翻页
    - 账号：账号页固定 3 个区域，OCR 名字（去掉末尾「上次登录」），
      识别区域同时就是点击区域
    - 角色：角色页固定 2 个区域，OCR 角色名，命中后点对应一侧的「登录角色」按钮

    每次点击都用最新一帧确认页面，点完等页面切走再继续，
    避免登录页 0.4s 切到服务器页期间用旧坐标点到区服列表。
    """

    # 点击页面按钮后，等待该页面特征消失（页面已经切走）的最长时间（秒）
    page_switch_timeout: int = 8

    # 区服
    SERVER_TAB_TIMEOUT = 8
    SERVER_TAB_WAIT = 0.5
    SERVER_SCAN_TIMEOUT = 120
    SERVER_SWIPE_WAIT = 1.0
    SERVER_MAX_SCROLL = 30
    # 两列区服同一行的 y 会差 1~2px，按行分组找左上角第一个区服
    SERVER_ROW_TOLERANCE = 40

    # 账号 / 角色
    ACCOUNT_SCAN_TIMEOUT = 20
    CHARACTER_SCAN_TIMEOUT = 20

    # 账号名末尾的「(上次登录)」文案
    ACCOUNT_SUFFIX = re.compile(r'\s*[（(]?\s*上次登录\s*[)）]?\s*$')
    # OCR 名称归一化：去掉空白、括号、标点
    NAME_PUNCTUATION = re.compile(r'[\s\u3000（）()【】\[\]：:·.、,，|]+')

    server_zone: int | None = None
    account_name: str = ''
    character_name: str = ''

    def _app_handle_login(self, timer: Timer = None) -> bool:
        logger.hr('App login (x7)')
        self.device.stuck_record_add('LOGIN_CHECK')
        self.load_login_target()

        while 1:
            # 超过登录总时长仍未进入游戏：抛错结束本轮登录，
            # 由 app_handle_login 统一按登录超时处理（推送通知 + TaskFailed）
            if timer is not None and timer.reached():
                logger.error(f'X7 login timeout after {timer.limit}s, still not in game')
                raise GameStuckError(f'X7 登录超过 {timer.limit}s 仍未进入游戏')
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
                self.handle_login_page()
                continue

            # 选择服务器页
            if self.appear(self.I_X7_SERVER_PAGE):
                self.handle_server_page()
                continue

            # 账号选择页（小号说明）
            if self.appear(self.I_X7_ACCOUNT_PAGE):
                self.handle_account_page()
                continue

            # 角色选择页
            if self.appear(self.I_X7_CHARACTER_PAGE):
                self.handle_character_page()
                continue

    # ---------------------------------------------------------------- 登录配置
    def load_login_target(self) -> None:
        """读取区服/账号/角色配置，区服只取数字部分（如 88区 -> 88）"""
        cfg = self.config.restart.login_x7_config
        raw_server = (cfg.server or '').strip()
        match = re.search(r'(\d+)', raw_server)
        if raw_server and not match:
            self.login_failed(f'区服配置无法解析: {raw_server}（应填写如 88区）')
        self.server_zone = int(match.group(1)) if match else None
        self.account_name = self.clean_account_name((cfg.account or '').strip())
        self.character_name = (cfg.character or '').strip()
        logger.attr(
            '登录目标',
            f'区服={raw_server or "上次登录"} '
            f'账号={self.account_name or "上次登录"} '
            f'角色={self.character_name or "左侧默认"}')

    def login_failed(self, message: str) -> None:
        """配置不匹配这类需要人工处理的失败：推送通知并按任务失败结束"""
        logger.error(message)
        self.config.notifier.push(title='重启任务Restart',
                                  content=f'<{self.config.config_name}> {message}')
        raise TaskFailed(message)

    def clean_account_name(self, text: str) -> str:
        """去掉账号名末尾的「(上次登录)」"""
        return self.ACCOUNT_SUFFIX.sub('', (text or '').strip()).strip()

    # ---------------------------------------------------------------- 登录页
    def handle_login_page(self) -> None:
        if not self.appear(self.I_X7_LOGIN_AGREE):
            logger.info('User agreement not checked, check it')
            self.click_agree_box()
            return
        if self.page_click(self.I_X7_LOGIN_ENTER):
            logger.info('Click login game')

    def click_agree_box(self, interval: float = 1) -> bool:
        """勾选用户协议：用最新一帧确认「还在登录页且没有勾选」再点击"""
        self.screenshot()
        if not self.appear(self.I_X7_LOGIN_ENTER) or self.appear(self.I_X7_LOGIN_AGREE):
            return False
        return self.click(self.C_X7_LOGIN_AGREE_CLICK, interval=interval)

    # ---------------------------------------------------------------- 区服选择
    def handle_server_page(self) -> None:
        logger.hr('Select server')
        timer = Timer(self.SERVER_SCAN_TIMEOUT).start()
        if not self.wait_server_tab():
            raise GameStuckError('Server tab not detected')

        # 先统一回到「我的服务器」
        if self.appear(self.I_X7_SERVER_TAB_ALL_SELECTED):
            if not self.click_server_tab(self.C_X7_SERVER_TAB_MINE, self.I_X7_SERVER_TAB_MINE_SELECTED):
                raise GameStuckError('Switch to my servers failed')

        if self.appear(self.I_X7_SERVER_TAB_MINE_SELECTED):
            if self.select_from_mine():
                return
            logger.info(f'区服 {self.server_zone} 不在我的服务器，切换到全部服务器')
            if not self.click_server_tab(self.C_X7_SERVER_TAB_ALL, self.I_X7_SERVER_TAB_ALL_SELECTED):
                raise GameStuckError('Switch to all servers failed')
        elif not self.appear(self.I_X7_SERVER_TAB_ALL_SELECTED):
            raise GameStuckError('Unknown server tab state')

        if self.appear(self.I_X7_SERVER_TAB_ALL_SELECTED):
            if self.select_from_all(timer):
                return

        target = f'{self.server_zone}区' if self.server_zone is not None else '上次登录区服'
        self.login_failed(f'未找到区服 [{target}]，请检查区服配置')

    def wait_server_tab(self, timeout: int = None) -> bool:
        """等待服务器页的选中态出现，判断当前在哪个 tab"""
        timer = Timer(timeout or self.SERVER_TAB_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_X7_SERVER_TAB_MINE_SELECTED) or self.appear(self.I_X7_SERVER_TAB_ALL_SELECTED):
                return True
            self.device.sleep(0.2)
        return False

    def click_server_tab(self, click_rule, selected_rule) -> bool:
        """切换服务器 tab，并等待目标 tab 的选中态出现"""
        logger.info(f'Click {click_rule.name}')
        self.click(click_rule)
        self.device.click_record_clear()
        timer = Timer(self.SERVER_TAB_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(selected_rule):
                logger.info(f'{selected_rule.name} selected')
                self.device.sleep(self.SERVER_TAB_WAIT)
                return True
            self.device.sleep(0.2)
        logger.warning(f'{selected_rule.name} not selected after {self.SERVER_TAB_TIMEOUT}s')
        return False

    def select_from_mine(self) -> bool:
        """
        我的服务器：先看「上次登录」，再看「已有角色服务器」列表

        :return: 已点击区服并进入下一页返回 True；没找到返回 False
        """
        logger.hr('Server list (mine)')
        self.screenshot()
        last_text = (self.O_X7_SERVER_LAST_LOGIN.ocr(self.device.image) or '').strip()
        last_zone = self.parse_zone(last_text)
        if not last_text:
            # 切到我的服务器后列表可能还没渲染
            for _ in range(5):
                self.device.sleep(0.4)
                self.screenshot()
                last_text = (self.O_X7_SERVER_LAST_LOGIN.ocr(self.device.image) or '').strip()
                last_zone = self.parse_zone(last_text)
                if last_text:
                    break
        logger.attr('上次登录区服', last_text or '-')

        if self.server_zone is None:
            # 没配区服：沿用上次登录
            return self.click_last_login_server(last_zone)
        if last_zone == self.server_zone:
            logger.info(f'上次登录区服就是 {self.server_zone}区')
            return self.click_last_login_server(last_zone)

        entries = self.mine_entries()
        for _ in range(5):
            if entries:
                break
            self.device.sleep(0.4)
            entries = self.mine_entries()
        for entry in entries:
            if entry.zone == self.server_zone:
                return self.click_server_entry(entry)
        return False

    def click_last_login_server(self, zone: int | None) -> bool:
        logger.info(f'Click last login server [{zone}区]' if zone is not None else 'Click last login server')
        self.click(self.C_X7_SERVER_LAST_LOGIN)
        self.device.click_record_clear()
        if self.wait_page_leave(self.I_X7_SERVER_PAGE):
            return True
        raise GameStuckError('Last login server click failed')

    def mine_entries(self) -> list[ServerEntry]:
        self.screenshot()
        entries = self.parse_server_entries(self.O_X7_SERVER_MINE_LIST)
        if entries:
            logger.attr('我的服务器', ','.join(str(entry.zone) for entry in entries))
        return entries

    def select_from_all(self, timer: Timer) -> bool:
        """
        全部服务器：区服按从大到小排列。

        先在当前屏找；没找到就滑一次，根据滑动前后左上角第一个区服的差值
        估算一次滑动跨过几个区服，再按需继续滑动，每滑一次都重新识别；
        划过头（目标区服比当前顶部还大）时反向滑动纠正。
        """
        logger.hr('Server list (all)')
        if self.server_zone is None:
            return False

        entries = self.all_entries()
        for _ in range(5):
            if entries:
                break
            self.device.sleep(0.4)
            entries = self.all_entries()
        if self.click_target_server(entries):
            return True
        if not entries:
            logger.warning('All server list is empty')
            return False
        if self.server_zone > entries[0].zone:
            logger.warning(f'目标区服 {self.server_zone} 大于列表最高区服 {entries[0].zone}')
            return False

        # 滑一次测步长
        first_before = entries[0].zone
        step = 0
        first_after = first_before
        for _ in range(2):
            self.swipe_server_next()
            entries = self.all_entries()
            if self.click_target_server(entries):
                return True
            if not entries:
                continue
            first_after = entries[0].zone
            step = first_before - first_after
            logger.attr('单次滑动区服数', step)
            if step > 0:
                break
            first_before = first_after
        if step <= 0:
            logger.warning('Server list did not scroll after swipe')
            return False

        visible_count = len({entry.zone for entry in entries})
        distance = first_after - self.server_zone
        remaining = 0 if distance <= visible_count - 1 else math.ceil(
            (distance - (visible_count - 1)) / step)
        logger.attr('预计还需滑动', remaining)

        for _ in range(self.SERVER_MAX_SCROLL):
            if timer.reached():
                logger.warning('Server scan timeout')
                break
            entries = self.all_entries()
            if self.click_target_server(entries):
                return True
            if not entries:
                self.device.sleep(0.5)
                continue

            top = max(entry.zone for entry in entries)
            bottom = min(entry.zone for entry in entries)
            if self.server_zone > top:
                logger.info(f'划过头了，目标在上方（当前顶部 {top}区），反向纠正')
                self.swipe_server_prev()
            elif self.server_zone < bottom:
                self.swipe_server_next()
            else:
                # 目标应在当前屏内：再识别一次，仍没有就继续向下找
                entries = self.all_entries()
                if self.click_target_server(entries):
                    return True
                self.swipe_server_next()
        return False

    def all_entries(self) -> list[ServerEntry]:
        self.screenshot()
        entries = self.parse_server_entries(self.O_X7_SERVER_ALL_LIST)
        if entries:
            logger.attr('全部服务器', ','.join(str(entry.zone) for entry in entries))
        return entries

    def parse_server_entries(self, rule) -> list[ServerEntry]:
        entries = []
        for item in rule.detect_and_ocr(self.device.image, logDisplay=False):
            text = (item.ocr_text or '').strip()
            zone = self.parse_zone(text)
            if zone is None:
                continue
            x, y = self.ocr_center(item, rule)
            entries.append(ServerEntry(zone, x, y, text))
        return self.sort_server_entries(entries)

    def sort_server_entries(self, entries: list[ServerEntry]) -> list[ServerEntry]:
        """
        按行分组、行内按 x 排序：第一个就是当前屏左上角的区服。
        两列同一行的 y 会有 1~2px 差异，不能直接按 (y, x) 排。
        """
        entries = sorted(entries, key=lambda entry: (entry.y, entry.x))
        rows = []
        for entry in entries:
            if rows and abs(entry.y - rows[-1][-1].y) <= self.SERVER_ROW_TOLERANCE:
                rows[-1].append(entry)
            else:
                rows.append([entry])
        ordered = []
        for row in rows:
            ordered.extend(sorted(row, key=lambda entry: entry.x))
        return ordered

    def click_target_server(self, entries: list[ServerEntry]) -> bool:
        for entry in entries:
            if entry.zone != self.server_zone:
                continue
            logger.info(f'Click server [{entry.zone}区] at ({entry.x:.0f},{entry.y:.0f})')
            self.device.click(x=int(entry.x), y=int(entry.y), control_name=f'x7_server_{entry.zone}')
            self.device.click_record_clear()
            if self.wait_page_leave(self.I_X7_SERVER_PAGE):
                return True
            raise GameStuckError(f'点击 {entry.zone}区 后服务器页没有切走')
        return False

    def click_server_entry(self, entry: ServerEntry) -> bool:
        logger.info(f'Click server [{entry.zone}区] at ({entry.x:.0f},{entry.y:.0f})')
        self.device.click(x=int(entry.x), y=int(entry.y), control_name=f'x7_server_{entry.zone}')
        self.device.click_record_clear()
        if self.wait_page_leave(self.I_X7_SERVER_PAGE):
            return True
        raise GameStuckError(f'点击 {entry.zone}区 后服务器页没有切走')

    def swipe_server_next(self) -> None:
        logger.info('Swipe server list down')
        self.swipe(self.S_X7_SERVER_SCROLL_NEXT)
        self.device.click_record_clear()
        self.device.sleep(self.SERVER_SWIPE_WAIT)

    def swipe_server_prev(self) -> None:
        logger.info('Swipe server list up (correct overshoot)')
        self.swipe(self.S_X7_SERVER_SCROLL_PREV)
        self.device.click_record_clear()
        self.device.sleep(self.SERVER_SWIPE_WAIT)

    @staticmethod
    def parse_zone(text: str) -> int | None:
        match = re.search(r'(\d+)\s*区', text or '')
        return int(match.group(1)) if match else None

    # ---------------------------------------------------------------- 账号选择
    def handle_account_page(self) -> None:
        """
        账号选择：固定识别 3 个区域，OCR 区域同时也是点击区域。
        配置了账号名就按名字选；没配则优先带「上次登录」的账号，否则第一个。
        """
        logger.hr('Select account')
        timer = Timer(self.ACCOUNT_SCAN_TIMEOUT).start()
        rules = (self.O_X7_ACCOUNT_NAME_1, self.O_X7_ACCOUNT_NAME_2, self.O_X7_ACCOUNT_NAME_3)

        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_X7_ACCOUNT_PAGE):
                return

            accounts = self.read_accounts(rules)
            if not accounts:
                self.device.sleep(0.5)
                continue

            if self.account_name:
                matched = next((account for account in accounts
                                if self.name_match(account.name, self.account_name)), None)
                if matched is not None and self.click_account(matched.rule):
                    return
            else:
                chosen = next((account for account in accounts if '上次登录' in account.raw),
                              accounts[0])
                if self.click_account(chosen.rule):
                    return
            self.device.sleep(0.5)

        target = self.account_name or '上次登录账号'
        self.login_failed(f'未找到账号 [{target}]，请检查账号配置')

    def read_accounts(self, rules: tuple) -> list[AccountEntry]:
        accounts = []
        for index, rule in enumerate(rules, 1):
            raw = (rule.ocr(self.device.image) or '').strip()
            if not raw:
                continue
            name = self.clean_account_name(raw)
            accounts.append(AccountEntry(index, rule, raw, name))
            logger.attr(f'账号{index}', raw)
        return accounts

    def click_account(self, rule) -> bool:
        logger.info(f'Click account region {rule.name}')
        self.click(rule)
        self.device.click_record_clear()
        if self.wait_page_leave(self.I_X7_ACCOUNT_PAGE):
            return True
        raise GameStuckError(f'点击账号区域 {rule.name} 后账号页没有切走')

    # ---------------------------------------------------------------- 角色选择
    def handle_character_page(self) -> None:
        """
        角色选择：固定识别左右 2 个角色名区域，命中后点对应一侧的登录按钮；
        没配置角色名则默认左侧角色。
        """
        logger.hr('Select character')
        timer = Timer(self.CHARACTER_SCAN_TIMEOUT).start()
        rules = (
            (self.O_X7_CHARACTER_NAME_1, self.C_X7_CHARACTER_LOGIN_LEFT),
            (self.O_X7_CHARACTER_NAME_2, self.C_X7_CHARACTER_LOGIN_RIGHT),
        )

        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_X7_CHARACTER_PAGE):
                return

            characters = self.read_characters(rules)
            if not characters:
                self.device.sleep(0.5)
                continue

            if self.character_name:
                matched = next((character for character in characters
                                if self.name_match(character.text, self.character_name)), None)
                if matched is not None and self.click_character_login(matched.login_rule):
                    return
            else:
                if self.click_character_login(self.C_X7_CHARACTER_LOGIN_LEFT):
                    return
            self.device.sleep(0.5)

        self.login_failed(f'未找到角色 [{self.character_name}]，请检查角色名配置')

    def read_characters(self, rules: tuple) -> list[CharacterEntry]:
        characters = []
        for index, (ocr_rule, login_rule) in enumerate(rules, 1):
            text = (ocr_rule.ocr(self.device.image) or '').strip()
            if not text:
                continue
            characters.append(CharacterEntry(index, text, login_rule))
            logger.attr(f'角色{index}', text)
        return characters

    def click_character_login(self, rule) -> bool:
        logger.info(f'Click {rule.name}')
        self.click(rule)
        self.device.click_record_clear()
        if self.wait_page_leave(self.I_X7_CHARACTER_PAGE):
            return True
        logger.warning(f'{rule.name} click did not leave character page')
        return False

    # ---------------------------------------------------------------- 名称匹配
    @classmethod
    def normalize_name(cls, text: str) -> str:
        return cls.NAME_PUNCTUATION.sub('', text or '')

    @classmethod
    def name_match(cls, ocr_text: str, name: str) -> bool:
        """
        名称匹配：包含即命中；更长的名字容忍 OCR 形近字/丢字
        （前两字一致 + 相似度 >= 0.6，如「好好干」被识别成「好好于」）
        """
        text = cls.normalize_name(ocr_text)
        name = cls.normalize_name(name)
        if not text or not name:
            return False
        if name in text:
            return True
        if len(name) < 3 or len(text) < len(name) - 1:
            return False
        if text[:2] != name[:2]:
            return False
        return difflib.SequenceMatcher(None, text[:len(name) + 1], name).ratio() >= 0.6

    # ---------------------------------------------------------------- 通用
    @staticmethod
    def ocr_center(item, rule) -> tuple[float, float]:
        """OCR 结果框中心 -> 全图绝对坐标"""
        xs = [point[0] for point in item.box]
        ys = [point[1] for point in item.box]
        return (
            rule.roi[0] + (min(xs) + max(xs)) / 2,
            rule.roi[1] + (min(ys) + max(ys)) / 2,
        )

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
        """等待页面特征消失（页面已经切走）"""
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
