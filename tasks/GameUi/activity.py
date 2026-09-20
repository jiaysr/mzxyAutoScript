# This Python file uses the following encoding: utf-8
"""
活动弹窗导航（ActivityNavigation）：

弹窗结构：
- 顶部 tab 栏：公告 / 活动 / 活跃 / 排行（可横向滚动）
- 内容区右侧子 tab 栏：推荐 / 强化 / 赚钱 / 练级 / 其他 / 世界首领 / 副本（可上下滚动）
- 切回顶部"活动"tab 时子 tab 会重置为第一个（推荐）

两者同样是"OCR 定位 -> 目标不可见时按顺序滚动查找 -> 点击"的模式，由 GameUi 混入本类使用。
"""
from time import sleep

from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_activity_active
from tasks.base_task import BaseTask


class ActivityNavigation(BaseTask, GameUiAssets):
    """活动弹窗（顶部 tab / 右侧子 tab）导航能力"""

    # 弹窗顶部 tab 顺序（用于滚动方向判断）
    ACTIVITY_TABS: list = ['公告', '活动', '活跃', '排行']
    # 弹窗右侧子 tab 顺序（用于滚动方向判断）
    ACTIVITY_SUBTABS: list = ['推荐', '强化', '赚钱', '练级', '其他', '世界首领', '副本']

    # ------------------------------------------------------------------ 顶部 tab 栏
    def ui_activity_tab_click(self, name: str, max_swipe: int = 4) -> bool:
        """点击活动弹窗顶部 tab（OCR 定位，支持横向滚动查找）"""
        for _ in range(max_swipe + 1):
            self.screenshot()
            coord = self.ui_activity_tab_find(name)
            if coord:
                logger.info(f'Click activity tab {name} at {coord}')
                self.device.click(x=coord[0], y=coord[1], control_name=f'ui_activity_tab_{name}')
                self.device.click_record_clear()
                return True
            if not self.ui_activity_tab_scroll(name):
                break
        logger.warning(f'Activity tab {name} not found')
        return False

    def ui_activity_tab_ocr(self) -> list:
        """OCR 活动弹窗顶部 tab 栏，返回识别结果（坐标相对 roi 裁剪）"""
        return self.O_ACTIVITY_TABS.detect_and_ocr(self.device.image, logDisplay=False)

    def ui_activity_tab_find(self, name: str) -> tuple | None:
        """返回顶部 tab 文字中心坐标，未找到返回 None"""
        roi = self.O_ACTIVITY_TABS.roi
        for item in self.ui_activity_tab_ocr():
            if item.ocr_text != name:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return x, y
        return None

    def ui_activity_tab_visible(self) -> list[str]:
        """当前可见的顶部 tab 名，按屏幕位置从左到右排序"""
        items = []
        for item in self.ui_activity_tab_ocr():
            if item.ocr_text in self.ACTIVITY_TABS:
                items.append((item.ocr_text, float(item.box[0][0])))
        items.sort(key=lambda x: x[1])
        return [name for name, _ in items]

    def ui_activity_tab_scroll(self, name: str) -> bool:
        """按目标 tab 相对可见 tab 的位置横向滚动一次"""
        if name not in self.ACTIVITY_TABS:
            return False
        visible = self.ui_activity_tab_visible()
        if not visible:
            return False
        target = self.ACTIVITY_TABS.index(name)
        indexes = [self.ACTIVITY_TABS.index(v) for v in visible]
        if target < min(indexes):
            p1, p2 = (300, 115), (700, 115)
        elif target > max(indexes):
            p1, p2 = (700, 115), (300, 115)
        else:
            return False
        logger.info(f'Scroll activity tabs to find {name}')
        self.device.swipe(p1=p1, p2=p2, control_name='ui_activity_tab_scroll')
        sleep(0.6)
        return True

    # ------------------------------------------------------------------ 右侧子 tab 栏
    def ui_activity_subtab_click(self, name: str, max_swipe: int = 4) -> bool:
        """点击活动弹窗右侧子 tab（OCR 定位，支持上下滚动查找）"""
        for _ in range(max_swipe + 1):
            self.screenshot()
            coord = self.ui_activity_subtab_find(name)
            if coord:
                logger.info(f'Click activity subtab {name} at {coord}')
                self.device.click(x=coord[0], y=coord[1], control_name=f'ui_activity_subtab_{name}')
                self.device.click_record_clear()
                return True
            if not self.ui_activity_subtab_scroll(name):
                break
        logger.warning(f'Activity subtab {name} not found')
        return False

    def ui_activity_subtab_ocr(self) -> list:
        """OCR 活动弹窗右侧子 tab 栏，返回识别结果（坐标相对 roi 裁剪）"""
        return self.O_ACTIVITY_SUBTABS.detect_and_ocr(self.device.image, logDisplay=False)

    def ui_activity_subtab_find(self, name: str) -> tuple | None:
        """返回子 tab 文字中心坐标，未找到返回 None"""
        roi = self.O_ACTIVITY_SUBTABS.roi
        for item in self.ui_activity_subtab_ocr():
            if item.ocr_text != name:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return x, y
        return None

    def ui_activity_subtab_visible(self) -> list[str]:
        """当前可见的子 tab 名，按屏幕位置从上到下排序"""
        items = []
        for item in self.ui_activity_subtab_ocr():
            if item.ocr_text in self.ACTIVITY_SUBTABS:
                items.append((item.ocr_text, float(item.box[0][1])))
        items.sort(key=lambda x: x[1])
        return [name for name, _ in items]

    def ui_activity_subtab_scroll(self, name: str) -> bool:
        """按目标子 tab 相对可见子 tab 的位置上下滚动一次"""
        if name not in self.ACTIVITY_SUBTABS:
            return False
        visible = self.ui_activity_subtab_visible()
        if not visible:
            return False
        target = self.ACTIVITY_SUBTABS.index(name)
        indexes = [self.ACTIVITY_SUBTABS.index(v) for v in visible]
        if target < min(indexes):
            p1, p2 = (1130, 320), (1130, 580)
        elif target > max(indexes):
            p1, p2 = (1130, 580), (1130, 320)
        else:
            return False
        logger.info(f'Scroll activity subtabs to find {name}')
        self.device.swipe(p1=p1, p2=p2, control_name='ui_activity_subtab_scroll')
        sleep(0.5)
        return True

    # ------------------------------------------------------------------ 活跃任务列表
    def active_task_completed(self, name: str) -> bool:
        """
        进入活动-活跃页，查找指定活跃任务是否已完成
        :return: 已完成返回 True；未完成或未找到返回 False
        """
        logger.hr('Check activity task')
        if not self.ui_goto(page_activity_active, timeout=40):
            raise GameStuckError('Activity page does not appear')

        # 列表回到顶部
        for _ in range(2):
            self.device.swipe(p1=(370, 300), p2=(370, 550))
            self.device.click_record_clear()
            self.device.sleep(0.4)

        for _ in range(5):
            self.screenshot()
            results = self.O_ACTIVITY_TASK_LIST.detect_and_ocr(self.device.image, logDisplay=False)
            status = self.parse_active_status(results, name)
            if status is not None:
                logger.info(f'Activity task [{name}] {"completed" if status else "not completed"}')
                return status
            logger.info(f'Activity task [{name}] not visible, scroll down')
            self.device.swipe(p1=(370, 550), p2=(370, 300))
            self.device.click_record_clear()
            self.device.sleep(0.6)

        logger.warning(f'Activity task [{name}] not found')
        return False

    @staticmethod
    def parse_active_status(results: list, name: str):
        """
        在活跃任务列表的 OCR 结果中查找任务行的状态
        :return: True=完成 / False=未完成 / None=未找到
        """
        name_y = None
        for item in results:
            if name in item.ocr_text:
                box = item.box
                name_y = float((box[0][1] + box[2][1]) / 2)
                break
        if name_y is None:
            return None
        for item in results:
            text = item.ocr_text.strip()
            if '完成' not in text:
                continue
            box = item.box
            y = float((box[0][1] + box[2][1]) / 2)
            if abs(y - name_y) <= 20:
                return text == '完成'
        return None
