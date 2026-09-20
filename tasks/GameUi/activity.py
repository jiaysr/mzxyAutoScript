# This Python file uses the following encoding: utf-8
"""
活动弹窗导航（ActivityNavigation）：

弹窗结构：
- 顶部 tab 栏：公告 / 活动 / 活跃 / 排行（可横向滚动）
- 内容区右侧子 tab 栏：推荐 / 强化 / 赚钱 / 练级 / 其他 / 世界首领 / 副本（可上下滚动）
- 切回顶部"活动"tab 时子 tab 会重置为第一个（推荐）

两者同样是"OCR 定位 -> 目标不可见时按顺序滚动查找 -> 点击"的模式，由 GameUi 混入本类使用。
"""
import difflib
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
    # 扫描屏数（列表 30 行以上，每屏约 5 行；同服竞技/跨服竞技 在列表靠后位置）
    ACTIVITY_SCAN_ROWS = 20
    # 每屏滚动距离（px）：行高约 78px，一次滚 2 行以内，避免快速甩动甩过头
    ACTIVITY_SCROLL_DISTANCE = 150
    # 滚动拆分段数与段间停顿
    ACTIVITY_SCROLL_STEPS = 3
    ACTIVITY_SCROLL_STEP_DELAY = 0.2
    # 滚动后等列表稳定再截图识别
    ACTIVITY_SCROLL_SETTLE = 0.8
    # 行名相似度阈值（容忍 OCR 形近字误识）
    ACTIVITY_NAME_SIMILARITY = 0.7

    def active_task_completed(self, name: str) -> bool:
        """
        进入活动-活跃页，查找指定活跃任务是否已完成
        :return: 已完成返回 True；未完成或未找到返回 False
        """
        logger.hr('Check activity task')
        if not self.ui_goto(page_activity_active, timeout=40):
            raise GameStuckError('Activity page does not appear')

        self.activity_list_to_top()

        last_rows = None
        for _ in range(self.ACTIVITY_SCAN_ROWS):
            completed, rows = self.activity_task_scan(name)
            if completed is not None:
                return completed
            if rows and rows == last_rows:
                # 滚动后内容没变，说明已经到底
                logger.warning(f'Activity task [{name}] list reached bottom')
                break
            last_rows = rows
            logger.info(f'Activity task [{name}] not visible, scroll down')
            self.activity_list_scroll()

        logger.warning(f'Activity task [{name}] not found')
        return False

    def activity_task_scan(self, name: str):
        """
        在当前可见的活跃任务行里查找目标任务（状态没识别出来时重截一张再试）
        :return: (True=完成 / False=未完成 / None=当前屏没有, 当前可见行名)
        """
        for _ in range(2):
            self.screenshot()
            results = self.O_ACTIVITY_TASK_LIST.detect_and_ocr(self.device.image, logDisplay=False)
            rows = self.parse_activity_rows(results)
            names = [row_name for row_name, _ in rows]
            logger.info(f'Activity rows: {names}')
            hit = False
            for row_name, completed in rows:
                if not self.activity_name_match(row_name, name):
                    continue
                hit = True
                if completed is None:
                    continue
                logger.info(f'Activity task [{row_name}] {"completed" if completed else "not completed"}')
                return completed, names
            if not hit:
                return None, names
            logger.warning(f'Activity task [{name}] status not recognized, retry')
            self.device.sleep(0.3)
        return None, names

    def activity_list_scroll(self, up: bool = False) -> None:
        """
        慢速滚动活跃任务列表，滚动后等列表稳定
        :param up: True=向上回顶，False=向下翻
        """
        distance = self.ACTIVITY_SCROLL_DISTANCE
        if up:
            p1, p2 = (370, 300), (370, 300 + distance)
        else:
            p1, p2 = (370, 300 + distance), (370, 300)
        self.ui_swipe_gentle(p1, p2, steps=self.ACTIVITY_SCROLL_STEPS,
                             step_delay=self.ACTIVITY_SCROLL_STEP_DELAY)
        self.device.sleep(self.ACTIVITY_SCROLL_SETTLE)

    def activity_list_to_top(self, max_swipe: int = 6) -> None:
        """
        把活跃任务列表滑到顶部（回顶不要求精度，用大步滑动；滑动后 OCR 内容不变即认为到顶）
        """
        last = None
        for _ in range(max_swipe):
            self.screenshot()
            texts = tuple(item.ocr_text for item in
                          self.O_ACTIVITY_TASK_LIST.detect_and_ocr(self.device.image, logDisplay=False))
            if texts == last:
                return
            last = texts
            self.device.swipe(p1=(370, 300), p2=(370, 550))
            self.device.click_record_clear()
            self.device.sleep(self.ACTIVITY_SCROLL_SETTLE)

    @staticmethod
    def parse_activity_rows(results: list) -> list[tuple[str, bool]]:
        """
        把活跃列表 OCR 结果按行分组，返回 [(行名, 是否完成)]（按屏幕位置从上到下）
        行名取每行最左侧文本，状态列取同行的「完成/未完成」
        """
        rows = []  # [[行名, y, 是否完成]]
        for item in results:
            box = item.box
            x = float((box[0][0] + box[1][0]) / 2)
            if x < 150:
                y = float((box[0][1] + box[2][1]) / 2)
                rows.append([item.ocr_text.strip(), y, None])
        for item in results:
            text = item.ocr_text.strip()
            if text not in ('完成', '未完成'):
                continue
            box = item.box
            y = float((box[0][1] + box[2][1]) / 2)
            for row in rows:
                if abs(row[1] - y) <= 20:
                    row[2] = text == '完成'
                    break
        rows.sort(key=lambda row: row[1])
        return [(row[0], row[2]) for row in rows]

    @classmethod
    def activity_name_match(cls, ocr_text: str, name: str) -> bool:
        """
        活跃任务名匹配：完全包含，或「前两字一致 + 相似度达标」（容忍 OCR 形近字误识）
        前两字必须一致，避免同服竞技/跨服竞技 这类只差一字的名称互相误判
        """
        if not ocr_text or not name:
            return False
        if name in ocr_text:
            return True
        if len(name) < 3 or len(ocr_text) < len(name) - 1:
            return False
        if ocr_text[:2] != name[:2]:
            return False
        candidate = ocr_text[:len(name)]
        return difflib.SequenceMatcher(None, candidate, name).ratio() >= cls.ACTIVITY_NAME_SIMILARITY
