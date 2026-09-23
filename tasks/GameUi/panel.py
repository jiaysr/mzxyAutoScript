# This Python file uses the following encoding: utf-8
"""
角色面板导航（PanelNavigation）：

- 左侧模块栏：可上下滚动
- 顶部 tab 栏：可横向滚动

两者都是"OCR 定位目标 -> 目标不可见时按顺序滚动查找 -> 点击 -> 校验 -> 重试"的模式，
由 GameUi 混入本类使用。
"""
from time import sleep

import cv2

from module.base.timer import Timer
from module.logger import logger
from module.ocr.base_ocr import enlarge_canvas
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import Page
from tasks.base_task import BaseTask


class PanelNavigation(BaseTask, GameUiAssets):
    """角色面板（左侧模块栏 / 顶部 tab 栏）导航能力"""

    # 当前页面（由 GameUi.ui_get_current_page 维护，tab 归属推断时读取）
    ui_current: Page = None
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
    # 侧栏 OCR 放大倍数（描边蓝字原尺寸识别易丢字）
    SIDEBAR_OCR_SCALE: int = 3

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
        """
        OCR 角色面板左侧模块栏，返回识别结果（坐标相对 roi 裁剪，已还原到原图尺寸）

        侧栏是带描边的蓝字，原尺寸识别会丢字（「物品」只识别出「品」），
        先把 roi 放大再识别（参考 WorldBoss 读目标名的做法）
        """
        rule = self.O_PANEL_SIDEBAR
        image = rule.crop(self.device.image, rule.roi)
        image = cv2.resize(image, None, fx=self.SIDEBAR_OCR_SCALE, fy=self.SIDEBAR_OCR_SCALE,
                           interpolation=cv2.INTER_CUBIC)
        items = []
        for item in rule.model.detect_and_ocr(enlarge_canvas(image)):
            if float(item.score) < rule.score:
                continue
            item.box = item.box / self.SIDEBAR_OCR_SCALE
            items.append(item)
        return items

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
            if self.ocr_name_pick(item.ocr_text, self.PANEL_SIDEBAR_ORDER) != name:
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
                coord = self.ui_tab_find(name, module)
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
            if self.ui_tab_selected(name, module):
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

    def ui_tab_find(self, name: str, module: str = None) -> tuple | None:
        """返回 tab 文字中心坐标，未找到返回 None"""
        roi = self.O_PANEL_TABS.roi
        tabs = self.PANEL_TABS.get(self.ui_tab_module(module)) or []
        for item in self.ui_tab_ocr():
            if self.ocr_name_pick(item.ocr_text, tabs) != name:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return x, y
        return None

    def ui_tab_selected(self, name: str, module: str = None) -> bool:
        """判断某 tab 是否处于选中态（选中 tab 文字更大、位置更靠下）"""
        roi = self.O_PANEL_TABS.roi
        tabs = self.PANEL_TABS.get(self.ui_tab_module(module)) or []
        for item in self.ui_tab_ocr():
            if self.ocr_name_pick(item.ocr_text, tabs) != name:
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
