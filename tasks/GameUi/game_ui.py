# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
"""
明珠轩辕页面寻路引擎

职责：
- 页面识别（ui_get_current_page）
- 页面图最短路径（反向 BFS：build_reverse_path_dict）
- 路径执行（_execute_path：点连线按钮 -> 等目标页出现）
- 未知页面脱困（ui_close / ui_safe_click）
- 统一操作入口（appear_then_operate，支持 Rule* 与 SidebarTarget/TabTarget/MenuTarget）

拆分出的导航能力（本类混入使用）：
- tasks/GameUi/panel.py    角色面板左侧模块栏 / 顶部 tab 栏
- tasks/GameUi/top_menu.py 右上角菜单
- tasks/GameUi/targets.py  页面连线目标类型
"""
import importlib
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from time import sleep

import random
from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.list import RuleList
from module.atom.ocr import RuleOcr
from module.base.decorator import run_once
from module.base.timer import Timer
from module.config.config_manual import ConfigManual
from module.config.utils import convert_to_underscore
from module.exception import (GameNotRunningError, GamePageUnknownError)
from module.logger import logger
from tasks.GameUi.page import Page, PageRegistry
from tasks.GameUi.activity import ActivityNavigation
from tasks.GameUi.panel import PanelNavigation
from tasks.GameUi.targets import (SidebarTarget, TabTarget, MenuTarget,
                                  ActivityTabTarget, ActivitySubTabTarget)
from tasks.GameUi.top_menu import TopMenuNavigation


class GameUi(PanelNavigation, TopMenuNavigation, ActivityNavigation):
    # 本任务在 ConfigManual.SCHEDULER_PRIORITY 中的名字（子类覆盖，用于让路判断）
    SCHEDULER_NAME: str = ''
    # 各任务的弹窗清理按钮：记录 MZXY 页面素材后根据自己的界面覆盖
    ui_close: list = []
    # 未知页面的兜底安全点击区域：没有配置时不做点击，只等待超时
    ui_safe_click: list = []

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

    # ------------------------------------------------------------------ 页面识别
    def ui_page_appear(self, page: Page, skip_first_screenshot: bool = True, interval: float = None):
        """判断当前页面是否为page"""
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

    # ------------------------------------------------------------------ 寻路
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
                        attempt = self.appear_then_operate(btn, interval=click_interval, skip_first_screenshot=False)
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

    # ------------------------------------------------------------------ 统一操作入口
    def appear_then_operate(self, target: RuleList | RuleImage | RuleGif | RuleOcr | RuleClick,
                            interval: float = None, skip_first_screenshot: bool = True):
        """
        出现对应目标执行操作(点击图像, 滑动列表至array第一个元素并点击, 点击OCR, 点击)
        :param target: 目标（Rule* 或连线目标 SidebarTarget/TabTarget/MenuTarget）
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
        elif isinstance(target, MenuTarget):
            operated = self.ui_menu_click(target.icon)
        elif isinstance(target, ActivityTabTarget):
            operated = self.ui_activity_tab_click(target.name)
        elif isinstance(target, ActivitySubTabTarget):
            operated = self.ui_activity_subtab_click(target.name)
        elif isinstance(target, RuleList):
            operated = self.list_appear_click(target, interval=interval)
        elif isinstance(target, (RuleImage, RuleGif)):
            operated = self.appear_then_click(target, interval=interval)
        elif isinstance(target, RuleOcr):
            operated = self.ocr_appear_click(target, interval=interval)
        elif isinstance(target, RuleClick):
            operated = self.click(target, interval=interval)
        return operated

    # ------------------------------------------------------------------ 通用弹窗与调度
    def reset_records(self) -> None:
        """
        长时间等待或连续滑动前，清空卡死与连点记录
        """
        self.device.stuck_record_clear()
        self.device.click_record_clear()

    def ui_swipe_gentle(self, p1: tuple, p2: tuple, steps: int = 3, step_delay: float = 0.2) -> None:
        """
        慢速滑动：把整段位移拆成多段小滑动，每段之间停顿
        （minitouch 的滑动速度固定且很快，整段一次滑容易甩过头、滑完立刻截图也认不准）
        :param p1: 起点
        :param p2: 终点
        :param steps: 拆分段数
        :param step_delay: 每段之间的停顿（秒）
        """
        points = [(int(p1[0] + (p2[0] - p1[0]) * i / steps),
                   int(p1[1] + (p2[1] - p1[1]) * i / steps)) for i in range(steps + 1)]
        for start, end in zip(points, points[1:]):
            self.device.swipe(p1=start, p2=end)
            self.device.click_record_clear()
            self.device.sleep(step_delay)

    def dialog_appear(self, rule: RuleOcr, text: str) -> bool:
        """
        精确检测弹窗文案（不用 ocr_appear：框架的 OCR filter 有逐字符兜底匹配，会误判）
        :param rule: 弹窗文案的 OCR 规则（如 self.O_DIALOG_TEXT）
        :param text: 要匹配的文案片段
        """
        results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        return any(text in result.ocr_text for result in results)

    def higher_priority_task_due(self) -> bool:
        """
        是否有调度优先级高于本任务、且已使能且已到期的任务（用于长时间等待时让路）
        优先级顺序取自 ConfigManual.SCHEDULER_PRIORITY，本任务名由 SCHEDULER_NAME 指定
        """
        now = datetime.now()
        for name in self.higher_priority_task_names():
            task = getattr(self.config.model, convert_to_underscore(name), None)
            scheduler = getattr(task, 'scheduler', None)
            if scheduler is None or not scheduler.enable:
                continue
            if scheduler.next_run <= now:
                logger.attr('Higher priority task', f'{name} {scheduler.next_run}')
                return True
        return False

    @classmethod
    def higher_priority_task_names(cls) -> list[str]:
        """调度优先级高于本任务的任务名列表"""
        names = [name.strip() for name in ConfigManual.SCHEDULER_PRIORITY.split('>') if name.strip()]
        if cls.SCHEDULER_NAME in names:
            return names[:names.index(cls.SCHEDULER_NAME)]
        return names


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from tasks.GameUi.page import page_activity_notice

    c = Config('oas1')
    d = Device(c)
    game = GameUi(config=c, device=d)
    game.ui_get_current_page()
    game.ui_goto(page_activity_notice)
    logger.info(f'Registered pages: {[str(page) for page in game.ui_pages]}')
