# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import importlib
import sys
from pathlib import Path

from datetime import datetime
from time import sleep

import random
from collections import deque
from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.list import RuleList
from module.atom.ocr import RuleOcr
from module.base.decorator import run_once
from module.base.timer import Timer
from module.exception import (GameNotRunningError, GamePageUnknownError)
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import Page, PageRegistry, SidebarTarget, TabTarget, page_main, page_item_bag, page_challenge
from tasks.base_task import BaseTask


class GameUi(BaseTask, GameUiAssets):
    ui_current: Page = None
    # 各任务的弹窗清理按钮：记录 MZXY 页面素材后根据自己的界面覆盖
    ui_close: list = []
    # 未知页面的兜底安全点击区域：没有配置时不做点击，只等待超时
    ui_safe_click: list = []
    # 角色面板左侧模块栏：从上到下的模块顺序（用于滚动方向判断）
    PANEL_SIDEBAR_ORDER: list = ['角色', '物品', '任务', '挑战', '辅助', '社交', '仙盟', '势力', '队伍', '系统']
    # 角色面板各模块的 tab 顺序（含横向滚动的隐藏 tab，用于滚动方向判断）
    PANEL_TABS: dict = {
        '角色': ['详情', '技能', '仙体', '称号', '道纹', '状态'],
        '物品': ['背包', '打造', '拍卖', '快捷'],
        '任务': ['任务', '任务特权', '任务加量'],
        '挑战': ['战场', '不周山', '无尽秘境', '仙府九重天', '斗仙阁'],
        '辅助': ['幻化', '金身', '坐骑', '生肖', '种植'],
        '社交': ['好友', '飞书', '结义', '夫妻', '师徒'],
        '仙盟': ['成员', '任务', '信息', '科技', '贡献', '招募', '任命', '批复'],
        '势力': ['信息', '状态', '指挥', '仓库', '管理', '禁言', '科技'],
        '队伍': ['队伍'],
        '系统': ['设置', '客服', '安全锁'],
    }
    # 当前所在模块（ui_sidebar_click 时记录，供 tab 滚动方向判断）
    ui_current_module: str = None

    def __init__(self, config, device):
        super().__init__(config, device)
        # 初始化时动态导入所有 page 模块
        self._import_all_pages()

    @staticmethod
    def _import_all_pages():
        """动态加载 tasks/**/page.py"""
        base_dir = Path(__file__).resolve().parent.parent  # tasks 目录
        for task_dir in base_dir.iterdir():
            if not task_dir.is_dir():
                continue
            page_file = task_dir / "page.py"
            if not page_file.exists():
                continue
            module_name = f"tasks.{task_dir.name}.page"
            # 已被正常 import 过的 page 模块不再重复执行，避免页面重复注册
            if module_name in sys.modules:
                continue
            spec = importlib.util.spec_from_file_location(module_name, page_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

    @property
    def ui_pages(self) -> list[Page]:
        return PageRegistry.all()

    def ui_page_appear(self, page: Page, skip_first_screenshot: bool = True, interval: float = None):
        """
        判断当前页面是否为page
        """
        self.maybe_screenshot(skip_first_screenshot)
        if isinstance(page.check_button, list):
            for button in page.check_button:
                if self.appear(button, interval):
                    return True
            return False
        return self.appear(page.check_button, interval)

    def ui_wait_until_appear(self, page: Page, timeout: float = 5, interval: float = 0.5,
                             skip_first_screenshot: bool = True) -> bool:
        """
        等待页面出现
        :param page: 等待的页面
        :param timeout: 超时时间
        :param interval: 检查间隔时间
        :param skip_first_screenshot:
        :return: 页面出现返回True, 否则返回False
        """
        logger.info(f'Waiting for {page}')
        timeout_timer = Timer(timeout).start()
        while not timeout_timer.reached():
            if self.ui_page_appear(page, skip_first_screenshot, interval=interval):
                return True
            skip_first_screenshot = False
        return False

    def ui_get_current_page(self, skip_first_screenshot=True) -> Page:
        """
        获取当前页面
        :param skip_first_screenshot:
        :return:
        """
        logger.info("UI get current page")

        @run_once
        def app_check():
            if not self.device.app_is_running():
                raise GameNotRunningError("Game not running")

        @run_once
        def minicap_check():
            if self.config.script.device.control_method == "uiautomator2":
                self.device.uninstall_minicap()

        @run_once
        def rotation_check():
            self.device.get_orientation()

        timeout = Timer(10, count=20).start()
        while 1:
            self.maybe_screenshot(skip_first_screenshot)
            skip_first_screenshot = False
            # 如果10S还没有到底，那么就抛出异常
            if timeout.reached():
                break
            # Known pages
            for page in self.ui_pages:
                if not page.check_button:
                    continue
                if self.ui_page_appear(page=page, interval=None):
                    logger.attr("UI", page.name)
                    self.ui_current = page
                    return page
            # Try to close unknown page
            if self.try_close_unknown_page():
                timeout = Timer(10, count=20).start()
            elif self.ui_safe_click:
                # entirely unknown page, click safe random area
                self.click(random.choice(self.ui_safe_click), interval=4)
            # wait to ui
            sleep(0.3)
            app_check()
            minicap_check()
            rotation_check()
        # Unknown page, need manual switching
        logger.warning("Unknown ui page")
        logger.attr("EMULATOR__SCREENSHOT_METHOD", self.config.script.device.screenshot_method)
        logger.attr("EMULATOR__CONTROL_METHOD", self.config.script.device.control_method)
        logger.warning("Starting from current page is not supported")
        logger.warning(f"Supported page: {[str(page) for page in self.ui_pages]}")
        logger.warning('Supported page: Any page with a "HOME" button on the upper-right')
        logger.critical("Please switch to a supported page before starting oas")
        raise GamePageUnknownError

    def ui_button_interval_reset(self, button):
        """
        Reset interval of some button to avoid mistaken clicks

        Args:
            button (Button):
        """
        if getattr(button, 'name', None) and button.name in self.interval_timer:
            self.interval_timer[button.name].reset()

    def build_reverse_path_dict(self, destination: Page) -> dict[Page, list[Page]]:
        """
        构建从每个页面到目标页面的最短路径（反向 BFS）

        Returns:
            dict[Page, list[Page]] -> {start_page: [page1, ...destinationPage], ...}
        """
        paths = {destination: [destination]}
        queue = deque([destination])
        while queue:
            cur = queue.popleft()
            for page in self.ui_pages:
                if page not in paths and cur in page.links:
                    # page -> cur
                    paths[page] = [page] + paths[cur]
                    queue.append(page)
        return paths

    def build_reverse_paths(self, destination: Page) -> list[tuple[Page, list[Page]]]:
        """
        构建从每个页面到目标页面的最短路径（反向 BFS）
        路径从短到长排序

        Returns:
            [(start_page, [page1, ...destinationPage]), ...]
        """
        paths = self.build_reverse_path_dict(destination)
        # 转换成列表并按路径长度排序, 短到长
        sorted_paths = sorted(paths.items(), key=lambda kv: len(kv[1]))
        return sorted_paths

    def ui_goto_page(self, dest_page: Page, confirm_wait=0, skip_first_screenshot=True, timeout: int = 60) -> bool:
        """前往指定page, 自动调用获取当前页面方法, 其他参数同ui_goto
        """
        self.ui_get_current_page()
        return self.ui_goto(dest_page, confirm_wait, skip_first_screenshot, timeout)

    def ui_goto(self, destination: Page, confirm_wait=0, skip_first_screenshot=True, timeout: int = 60) -> bool:
        """
        Args:
            destination (Page):
            confirm_wait:
            skip_first_screenshot:
        :return: find destination page or timeout reached
        """
        logger.hr(f"UI goto {destination}")
        # 初始化
        timeout_timer = Timer(timeout).start()
        confirm_timer = Timer(confirm_wait, count=int(confirm_wait // 0.5)).start()
        close_unknown_timer = Timer(3).start()
        # 构建路径映射
        path_dict = self.build_reverse_path_dict(destination)

        found = False
        while not timeout_timer.reached():
            if found:
                confirm_timer.wait()
                return True
            confirm_timer.reset()
            path = path_dict.get(self.ui_current, None)
            # 找不到路径则重新获取页面重试
            if not path:
                self.ui_get_current_page(skip_first_screenshot)
                continue
            skip_first_screenshot = False
            logger.info(f"Current page: {self.ui_current}. Following shortest path:")
            show_paths: str = ' -> '.join([p.name for p in path])
            logger.info(f"{show_paths}")
            # 遍历路径
            found = self._execute_path(path, timeout_timer)
            if not found:
                if close_unknown_timer.reached_and_reset():
                    self.try_close_unknown_page(skip_screenshot=False)
                    self.ui_current = None
        else:
            logger.error(f'Cannot goto page[{destination}], timeout[{timeout}s] reached')
        return False

    def try_close_unknown_page(self, skip_screenshot: bool = True):
        """
        尝试关闭未知界面
        :return: 执行了关闭返回True, 否则False
        """
        self.maybe_screenshot(skip_screenshot)
        timer = Timer(None).start()
        for close in self.ui_close:
            if self.appear_then_click(close, interval=1.5):
                logger.warning('Trying to switch to supported page')
                logger.info(f'[{timer.current():.1f}s]Click {close} on {self.ui_current} success')
                return True
        return False

    def _execute_path(self, path: list, timeout_timer):
        """
        执行路径
        :param path: currentPage,page1,page2,...,destinationPage
        :param timeout_timer: 超时定时器
        :return: currentPage==destinationPage
        """
        for i, current_page in enumerate(path):
            if timeout_timer.reached():
                return False
            # 当前页不等于路径中对应页, 尝试下一页
            if self.ui_current != current_page:
                continue
            self.run_additional(current_page, interval=0.6, skip_first_screenshot=False)
            # 如果已经是最后一页，不再跳转
            if i == len(path) - 1:
                if len(path) == 1:
                    logger.info(f'Page arrived {current_page}')
                break
            next_page = path[i + 1]
            logger.info(f'Page switch: {current_page} -> {next_page}')
            # 获取页面跳转操作
            button = current_page.links.get(next_page)
            if not button:
                logger.warning(f"No link from {current_page} to {next_page}")
                continue
            # 跳转页面
            max_wait_timer = Timer(6).start()
            logger.info(f'Wait appear and operate {button} on {current_page}')
            max_attempts_per_button = 3  # 每个按钮最多尝试 3 次
            attempts_list = [0] * len(button) if isinstance(button, list) else None
            while not max_wait_timer.reached():
                if timeout_timer.reached():
                    return False
                if isinstance(button, list):
                    for idx, btn in enumerate(button):
                        click_interval = 2.5 if attempts_list[idx] >= max_attempts_per_button else 0.8
                        attempt =  self.appear_then_operate(btn, interval=click_interval, skip_first_screenshot=False)
                        if attempt:
                            attempts_list[idx] += 1
                        # 只要第一个成功就跳出
                        if attempt and idx == 0:
                            break
                if self.appear_then_operate(button, interval=0.8, skip_first_screenshot=False):
                    break
            else:
                logger.warning(f'Failed recognize {button} on {current_page}')
                self.ui_get_current_page(skip_first_screenshot=False)
                # 当前页面不是对应路径的页面, 则尝试下一个页面
                if self.ui_current != current_page:
                    continue
            max_wait_timer.reset()
            while not max_wait_timer.reached():
                if timeout_timer.reached():
                    return False
                if self.ui_wait_until_appear(next_page, timeout=2.5, skip_first_screenshot=False):
                    logger.info(f'[{max_wait_timer.current():.1f}s]Page arrived {next_page}')
                    self.ui_current = next_page
                    break
            else:
                # 重新获取当前页
                self.ui_get_current_page(skip_first_screenshot=False)
        return self.ui_current == path[-1]

    def run_additional(self, page: Page, interval: float = None, skip_first_screenshot: bool = True):
        """执行页面附加操作"""
        if not page.additional:
            return
        for btn in page.additional:
            # 支持反向条件操作: [条件元素, 操作元素, True]。不出现条件图片时点击另一个区域。
            if isinstance(btn, list) and len(btn) == 3 and btn[2] is True:
                condition, action, invert = btn
                self.maybe_screenshot(skip_first_screenshot)
                if not self.appear(condition):
                    if self.appear_then_operate(action, interval=interval, skip_first_screenshot=False):
                        logger.info(f'Page {page} additional invert conditional not {condition} -> {action} executed')
                        skip_first_screenshot = False
            # 支持复合条件操作: [条件元素, 操作元素]。出现条件图片时点击另一个区域。
            elif isinstance(btn, list) and len(btn) == 2:
                condition, action = btn
                self.maybe_screenshot(skip_first_screenshot)
                if self.appear(condition):
                    if self.appear_then_operate(action, interval=interval, skip_first_screenshot=False):
                        logger.info(f'Page {page} additional conditional {condition} -> {action} executed')
                        skip_first_screenshot = False
            elif self.appear_then_operate(btn, interval=interval, skip_first_screenshot=skip_first_screenshot):
                logger.info(f'Page {page} additional {btn} clicked')
                skip_first_screenshot = False

    # ------------------------------------------------------------------ 左侧模块栏
    def ui_sidebar_click(self, name: str, max_swipe: int = 4, interval: float = None) -> bool:
        """
        点击角色面板左侧模块栏中的模块（OCR 定位，支持滚动查找）
        :param name: 模块名，如 '物品'
        :param max_swipe: 最多滚动次数
        :param interval: 点击间隔
        :return: 是否点击成功
        """
        if interval:
            key = f'ui_sidebar_{name}'
            if key in self.interval_timer:
                if self.interval_timer[key].limit != interval:
                    self.interval_timer[key] = Timer(interval)
            else:
                self.interval_timer[key] = Timer(interval)
            if not self.interval_timer[key].reached():
                return False
        for attempt in range(3):
            coord = None
            for _ in range(max_swipe + 1):
                self.screenshot()
                coord = self.ui_sidebar_find(name)
                if coord:
                    logger.info(f'Click sidebar module {name} at {coord}')
                    self.device.click(x=coord[0], y=coord[1], control_name=f'ui_sidebar_{name}')
                    self.device.click_record_clear()
                    break
                if not self.ui_sidebar_scroll(name):
                    break
            if not coord:
                break
            # 点击后校验模块是否切换成功，失败则重试
            if self.ui_wait_module(name, timeout=3):
                self.ui_current_module = name
                if interval:
                    self.interval_timer[f'ui_sidebar_{name}'].reset()
                return True
            logger.warning(f'Sidebar module {name} not switched, retry {attempt + 1}')
            sleep(0.3)
        logger.warning(f'Sidebar module {name} not found')
        return False

    def ui_wait_module(self, name: str, timeout: float = 3) -> bool:
        """等待面板切换到指定模块（通过 tab 栏识别校验）"""
        if name not in self.PANEL_TABS:
            sleep(0.5)
            return True
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.ui_tab_guess_module() == name:
                return True
            sleep(0.2)
        return False

    def ui_sidebar_ocr(self) -> list:
        """OCR 角色面板左侧模块栏，返回识别结果（坐标相对 roi 裁剪）"""
        return self.O_PANEL_SIDEBAR.detect_and_ocr(self.device.image, logDisplay=False)

    def ui_sidebar_visible(self) -> list[str]:
        """当前侧栏可见的模块名，按屏幕位置从上到下排序"""
        items = []
        for item in self.ui_sidebar_ocr():
            if item.ocr_text in self.PANEL_SIDEBAR_ORDER:
                items.append((item.ocr_text, float(item.box[0][1])))
        items.sort(key=lambda x: x[1])
        return [name for name, _ in items]

    def ui_sidebar_find(self, name: str) -> tuple | None:
        """返回模块文字中心坐标，未找到返回 None"""
        roi = self.O_PANEL_SIDEBAR.roi
        for item in self.ui_sidebar_ocr():
            if item.ocr_text != name:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return x, y
        return None

    def ui_sidebar_scroll(self, name: str) -> bool:
        """按目标模块相对可见模块的位置滚动侧栏一次"""
        visible = self.ui_sidebar_visible()
        order = self.PANEL_SIDEBAR_ORDER
        if not visible or name not in order:
            return False
        target = order.index(name)
        indexes = [order.index(v) for v in visible]
        if target < min(indexes):
            p1, p2 = (120, 320), (120, 600)
        elif target > max(indexes):
            p1, p2 = (120, 600), (120, 320)
        else:
            return False
        logger.info(f'Scroll sidebar to find {name}')
        self.device.swipe(p1=p1, p2=p2, control_name='ui_sidebar_scroll')
        sleep(0.5)
        return True

    # ------------------------------------------------------------------ 顶部 tab 栏
    def ui_tab_click(self, name: str, module: str = None, max_swipe: int = 6, interval: float = None) -> bool:
        """
        点击当前模块顶部 tab 栏中的 tab（OCR 定位，支持横向滚动查找）
        :param name: tab 名，如 '打造'
        :param module: 所属模块，默认取当前页面/当前记录的模块
        :param max_swipe: 最多滚动次数
        :param interval: 点击间隔
        :return: 是否点击成功
        """
        if interval:
            key = f'ui_tab_{name}'
            if key in self.interval_timer:
                if self.interval_timer[key].limit != interval:
                    self.interval_timer[key] = Timer(interval)
            else:
                self.interval_timer[key] = Timer(interval)
            if not self.interval_timer[key].reached():
                return False
        for attempt in range(3):
            coord = None
            for _ in range(max_swipe + 1):
                self.screenshot()
                coord = self.ui_tab_find(name)
                if coord:
                    break
                if not self.ui_tab_scroll(name, module):
                    break
            if not coord:
                break
            logger.info(f'Click tab {name} at {coord}')
            self.device.click(x=coord[0], y=coord[1], control_name=f'ui_tab_{name}')
            self.device.click_record_clear()
            # 点击后校验 tab 是否选中，失败则重试
            sleep(0.4)
            self.screenshot()
            if self.ui_tab_selected(name):
                if interval:
                    self.interval_timer[f'ui_tab_{name}'].reset()
                return True
            logger.warning(f'Tab {name} not selected, retry {attempt + 1}')
        logger.warning(f'Tab {name} not found')
        return False

    def ui_tab_ocr(self) -> list:
        """OCR 顶部 tab 栏，返回识别结果（坐标相对 roi 裁剪）"""
        return self.O_PANEL_TABS.detect_and_ocr(self.device.image, logDisplay=False)

    def ui_tab_visible(self) -> list[str]:
        """当前可见的 tab 名，按屏幕位置从左到右排序"""
        items = []
        for item in self.ui_tab_ocr():
            items.append((item.ocr_text, float(item.box[0][0])))
        items.sort(key=lambda x: x[1])
        return [name for name, _ in items]

    def ui_tab_find(self, name: str) -> tuple | None:
        """返回 tab 文字中心坐标，未找到返回 None"""
        roi = self.O_PANEL_TABS.roi
        for item in self.ui_tab_ocr():
            if item.ocr_text != name:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return x, y
        return None

    def ui_tab_selected(self, name: str) -> bool:
        """判断某 tab 是否处于选中态（选中 tab 文字更大、位置更靠下）"""
        roi = self.O_PANEL_TABS.roi
        for item in self.ui_tab_ocr():
            if item.ocr_text != name:
                continue
            box = item.box
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return y >= 44
        return False

    def ui_tab_guess_module(self) -> str | None:
        """根据当前可见 tab 推断所属模块"""
        visible = self.ui_tab_visible()
        if not visible:
            return None
        best, best_hit = None, 0
        for name, tabs in self.PANEL_TABS.items():
            hit = sum(1 for text in visible if text in tabs)
            if hit > best_hit:
                best, best_hit = name, hit
        if best_hit == 0:
            return None
        if len(visible) == 1:
            return best
        return best if best_hit >= 2 else None

    def ui_tab_module(self, module: str = None) -> str:
        """确定 tab 所属模块：显式参数 > 当前页面 module > 最近点击的模块 > 可见 tab 推断"""
        if module and module in self.PANEL_TABS:
            return module
        page_module = getattr(self.ui_current, 'module', None)
        if page_module in self.PANEL_TABS:
            return page_module
        if self.ui_current_module in self.PANEL_TABS:
            return self.ui_current_module
        return self.ui_tab_guess_module()

    def ui_tab_scroll(self, name: str, module: str = None) -> bool:
        """按目标 tab 相对可见 tab 的位置横向滚动一次"""
        module = self.ui_tab_module(module)
        if not module:
            logger.warning(f'Unknown module for tab {name}')
            return False
        tabs = self.PANEL_TABS[module]
        if name not in tabs:
            logger.warning(f'Tab {name} not in module {module}')
            return False
        visible = [text for text in self.ui_tab_visible() if text in tabs]
        if not visible:
            return False
        target = tabs.index(name)
        indexes = [tabs.index(v) for v in visible]
        if target < min(indexes):
            p1, p2 = (650, 50), (900, 50)
        elif target > max(indexes):
            p1, p2 = (900, 50), (650, 50)
        else:
            return False
        logger.info(f'Scroll tabs to find {name}')
        self.device.swipe(p1=p1, p2=p2, control_name='ui_tab_scroll')
        sleep(0.6)
        return True

    def appear_then_operate(self, target: RuleList | RuleImage | RuleGif | RuleOcr | RuleClick,
                            interval: float = None, skip_first_screenshot: bool = True):
        """
        出现对应目标执行操作(点击图像, 滑动列表至array第一个元素并点击, 点击OCR, 点击)
        :param target: 目标
        :param interval: 间隔
        :param skip_first_screenshot: 是否跳过首次截图
        :return: 是否成功操作
        """
        self.maybe_screenshot(skip_first_screenshot)
        operated = False
        if isinstance(target, SidebarTarget):
            operated = self.ui_sidebar_click(target.name, interval=interval)
        elif isinstance(target, TabTarget):
            operated = self.ui_tab_click(target.name, module=target.module, interval=interval)
        elif isinstance(target, RuleList):
            operated = self.list_appear_click(target, interval=interval)
        elif isinstance(target, (RuleImage, RuleGif)):
            operated = self.appear_then_click(target, interval=interval)
        elif isinstance(target, RuleOcr):
            operated = self.ocr_appear_click(target, interval=interval)
        elif isinstance(target, RuleClick):
            operated = self.click(target, interval=interval)
        return operated


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    game = GameUi(config=c, device=d)
    game.ui_get_current_page()
    game.ui_goto(page_challenge)
    logger.info(f'Registered pages: {[str(page) for page in game.ui_pages]}')
