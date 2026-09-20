# This Python file uses the following encoding: utf-8
"""
背包导航（BagNavigation）：

- `bag_find_item(name)` 在物品-背包页查找指定物品，返回物品图标中心坐标
- `bag_click_item(name)` 查找并点击物品

背包格子几何（1280x720）：
- 4 列，列图标中心 x = 304 / 541 / 778 / 1015（列距 237px）
- 物品格高 96px + 行间隔 24px（行距 120px），第一行图标中心 y = 155
- 物品名文字中心在图标中心右侧 111px（名字可能两行，取文字框中心行吸附即可）
- 网格可视区：x 255..1180，y 105..555（高 450px，约 3.7 行）

滚动使用 `ui_swipe_gentle`（慢速分段 + 滚动后等待布局稳定），每屏滚动约 2 行。
"""
from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_item_bag
from tasks.base_task import BaseTask


class BagNavigation(BaseTask, GameUiAssets):

    # 网格几何
    BAG_ROW_CENTER = 155
    BAG_ROW_PITCH = 120
    BAG_COLUMN_CENTERS = (304, 541, 778, 1015)
    BAG_ICON_TEXT_OFFSET = 111
    # 滚动与扫描
    BAG_SCROLL_DISTANCE = 240
    BAG_SCROLL_CENTER = 345
    BAG_SCAN_SCREENS = 18
    BAG_SETTLE = 1.0
    # 整理背包后的等待（整理动画 + 重新排布）
    BAG_SORT_SETTLE = 2.0
    # 物品详情面板的关闭按钮
    BAG_PANEL_CLOSE = (1075, 175)
    # 物品详情面板里的使用按钮文案（面板还有 设置快捷 / 永久丢弃）
    BAG_PANEL_USE_TEXTS = ('立即使用', '全部使用')

    def bag_find_item(self, name: str):
        """
        进入物品-背包页查找指定物品（进背包先点整理背包）
        :return: (x, y) 物品图标中心；未找到返回 None
        """
        logger.hr('Find bag item')
        if not self.ui_goto(page_item_bag, timeout=40):
            raise GameStuckError('Item bag page does not appear')
        self.device.sleep(self.BAG_SETTLE)
        self.bag_close_item_panel()
        self.bag_sort()

        last_rows = None
        for _ in range(self.BAG_SCAN_SCREENS):
            coord, rows = self.bag_item_scan(name)
            if coord:
                return coord
            if rows and rows == last_rows:
                logger.warning(f'Bag item [{name}] list reached bottom')
                break
            last_rows = rows
            logger.info(f'Bag item [{name}] not visible, scroll down')
            self.bag_scroll()

        logger.warning(f'Bag item [{name}] not found')
        return None

    def bag_click_item(self, name: str) -> bool:
        """
        查找并点击背包物品
        :return: 是否点到物品
        """
        coord = self.bag_find_item(name)
        if coord is None:
            return False
        logger.info(f'Click bag item [{name}] at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name=f'bag_item_{name}')
        self.device.click_record_clear()
        return True

    def bag_use_item(self, name: str) -> bool:
        """
        查找并点击背包物品，然后在详情面板点「立即使用」
        :return: 是否点到「立即使用」
        """
        if not self.bag_click_item(name):
            return False
        self.device.sleep(self.BAG_SETTLE)
        return self.bag_panel_use()

    def bag_panel_use(self, timeout: int = 5) -> bool:
        """
        物品详情面板：OCR 定位「立即使用」并点击
        （面板文案里有「使用后可参与...」的说明，所以要精确匹配按钮文字）
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            results = self.O_BAG_GRID.detect_and_ocr(self.device.image, logDisplay=False)
            for item in results:
                text = item.ocr_text.strip()
                if text not in self.BAG_PANEL_USE_TEXTS and not ('立即' in text and len(text) <= 5):
                    continue
                box = item.box
                x = (min(point[0] for point in box) + max(point[0] for point in box)) / 2 + self.O_BAG_GRID.roi[0]
                y = (min(point[1] for point in box) + max(point[1] for point in box)) / 2 + self.O_BAG_GRID.roi[1]
                logger.info(f'Click bag panel use [{text}] at ({x:.0f}, {y:.0f})')
                self.device.click(x=x, y=y, control_name='bag_panel_use')
                self.device.click_record_clear()
                return True
            if timer.reached():
                logger.warning('Bag panel use button not found')
                return False
            self.device.sleep(self.BAG_SETTLE)

    def bag_item_scan(self, name: str):
        """
        在当前可见的背包格里查找物品
        :return: ((x, y) 图标中心 / None, 当前可见物品名列表)
        """
        self.screenshot()
        results = self.O_BAG_GRID.detect_and_ocr(self.device.image, logDisplay=False)
        rows = self.parse_bag_items(results)
        names = [text for text, _, _ in rows]
        logger.info(f'Bag rows: {names}')
        for text, x, y in rows:
            if not self.ocr_name_match(text, name):
                continue
            coord = self.bag_item_coord(x, y)
            logger.info(f'Bag item [{text}] found at {coord}')
            return coord, names
        return None, names

    def bag_close_item_panel(self) -> None:
        """
        关掉打开着的物品详情面板（面板会挡住背包格子并且不响应滚动）
        """
        self.screenshot()
        texts = [item.ocr_text for item in self.O_BAG_GRID.detect_and_ocr(self.device.image, logDisplay=False)]
        if not any(mark in text for text in texts for mark in ('立即使用', '全部使用', '永久丢弃')):
            return
        logger.info('Bag item panel is open, close it')
        self.device.click(x=self.BAG_PANEL_CLOSE[0], y=self.BAG_PANEL_CLOSE[1], control_name='bag_panel_close')
        self.device.click_record_clear()
        self.device.sleep(self.BAG_SETTLE)

    def bag_sort(self) -> None:
        """
        点击整理背包，等整理动画结束后再开始找物品
        """
        logger.info('Click sort bag')
        self.click(self.C_BAG_SORT)
        self.device.click_record_clear()
        self.device.sleep(self.BAG_SORT_SETTLE)

    def bag_scroll(self) -> None:
        """
        慢速向下滚动一屏（约 2 行），滚动后等布局稳定
        """
        center = self.BAG_SCROLL_CENTER
        distance = self.BAG_SCROLL_DISTANCE
        self.ui_swipe_gentle(p1=(630, center + distance // 2), p2=(630, center - distance // 2))
        self.device.sleep(self.BAG_SETTLE)

    def parse_bag_items(self, results: list) -> list[tuple[str, float, float]]:
        """
        整理背包 OCR 结果：[(文本, 中心 x, 中心 y)]，按从上到下、从左到右排序
        """
        roi = self.O_BAG_GRID.roi
        items = []
        for item in results:
            box = item.box
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            x = (min(xs) + max(xs)) / 2 + roi[0]
            y = (min(ys) + max(ys)) / 2 + roi[1]
            items.append((item.ocr_text.strip(), x, y))
        items.sort(key=lambda it: (round(it[2] / 30), it[1]))
        return items

    def bag_item_coord(self, text_x: float, text_y: float) -> tuple:
        """
        物品名文字中心 -> 所在格子的图标中心（列按列距吸附，行按行距吸附）
        """
        centers = self.BAG_COLUMN_CENTERS
        column = round((text_x - self.BAG_ICON_TEXT_OFFSET - centers[0]) / (centers[1] - centers[0]))
        column = min(max(column, 0), len(centers) - 1)
        row = round((text_y - self.BAG_ROW_CENTER) / self.BAG_ROW_PITCH)
        return centers[column], self.BAG_ROW_CENTER + row * self.BAG_ROW_PITCH
