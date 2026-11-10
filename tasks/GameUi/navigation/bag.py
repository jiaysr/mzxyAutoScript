# This Python file uses the following encoding: utf-8
"""
背包导航（BagNavigation）：找物品 / 读物品格 / 用物品

**寻物优先走模板匹配**（模板 = 物品图标 + 第一行名字，实测 200x40 左右）：

- `bag_find_item(name, rule)`：先按模板逐屏匹配，命中后由命中框反推格子图标中心；
  模板没命中再退回 OCR 名字匹配（背包里可能出现"绑定"等变体名字，模板更稳）
- `bag_scan_rule(rule, on_hit)`：逐屏模板扫描的公共外壳（进背包 -> 关面板 -> 整理
  -> 逐屏匹配 -> 滚动 -> 到底停止），仙府九重天读碎片数量就挂在 on_hit 上
- `bag_use_item(name, rule)`：找到后点开物品详情面板，再点「立即使用/全部使用」

背包格子几何（1280x720，真机实测）：
- 4 列，列图标中心 x = 304 / 541 / 778 / 1015（列距 237px）
- 行：格子文字区顶 y = 110 / 228 / 350 / 471（行距约 120px）；
  物品名文字中心在图标中心右侧 111px
- 图标中心相对行顶约 25px（模板命中框左边也约在图标中心 - 25px）
- 网格可视区：x 255..1180，y 105..555（高 450px，约 3.7 行）

滚动沿用仙府那套**整页 swipe**（`(490,570) -> (490,80)` + 等 1.2s）：整页滚动后
每行在屏幕上的位置仍然固定（行 top=110/228/350/471），命中框反推格子才准；
改成小步滚动会让固定区域整体偏移（仙府文档里实测过）。
"""
import re

import cv2

from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.core.ocr import OcrHelpers
from tasks.GameUi.pages.page import page_item_bag
from tasks.base_task import BaseTask


class BagNavigation(BaseTask, GameUiAssets):

    # 网格几何（列图标中心 / 行文字区顶 / 名字文字相对图标中心的偏移）
    BAG_ROW_CENTER = 155
    BAG_ROW_PITCH = 120
    BAG_COLUMN_CENTERS = (304, 541, 778, 1015)
    BAG_ROW_TOPS = (110, 228, 350, 471)
    BAG_ICON_TEXT_OFFSET = 111
    # 模板命中框左边 -> 图标中心的偏移；图标中心相对行顶的偏移
    BAG_PICK_ICON_DX = 25
    BAG_PICK_ICON_HALF = 25
    # 命中格的文字区 / 数量区（相对列图标中心、行顶）
    BAG_TEXT_DX = 48
    BAG_TEXT_SIZE = (152, 118)
    BAG_COUNT_DX = 22
    BAG_COUNT_DY = 55
    BAG_COUNT_SIZE = (35, 30)

    # 翻页：整页 swipe（仙府实测写法，别改成小步滚动）
    # 整页滚动后每一行的屏幕位置是固定的，行 top=110/228/350/471 才成立；
    # 一次滚 3 行那种会让固定区域对不上（仙府文档里踩过）。
    BAG_SCROLL_P1 = (490, 570)
    BAG_SCROLL_P2 = (490, 80)
    BAG_SCROLL_SETTLE = 1.2
    BAG_SCAN_SCREENS = 18
    BAG_SETTLE = 1.0
    # 整理背包后的等待（整理动画 + 重新排布）
    BAG_SORT_SETTLE = 2.0

    # 物品详情面板
    BAG_PANEL_CLOSE = (1075, 175)
    # 面板里的使用按钮文案（面板还有 设置快捷 / 永久丢弃）
    BAG_PANEL_USE_TEXTS = ('立即使用', '全部使用')
    # 判断「详情面板开着」的标志（滚动/查找前要关掉它）
    BAG_PANEL_MARKS = ('立即使用', '全部使用', '永久丢弃')
    BAG_PANEL_TIMEOUT = 4.0

    # ---------------------------------------------------------------- 公共外壳
    def bag_open_ready(self) -> None:
        """
        进物品-背包页并收拾到可扫描状态：关残留详情面板 -> 整理

        **已经在背包页时先重置**：背包是长列表，游戏只在「重新进入」时才回到顶部，
        停在当前滚动位置直接扫描会漏掉上面的物品；重置办法是点左侧别的模块
        （角色）再点回「物品」，比手动往回滑稳得多（仙府也是这么做的）。
        """
        logger.hr('Open bag', level=2)
        if not self.ui_page_appear(page_item_bag, skip_first_screenshot=False):
            if not self.ui_goto(page_item_bag, timeout=40):
                raise GameStuckError('Item bag page does not appear')
            self.device.sleep(self.BAG_SETTLE)
        # 无论怎么进来的，都点一次左侧「物品」页签把模块重置到第一个 tab（背包）：
        # 已经在背包页时这是唯一能让列表回顶部的方式；刚进来时是一次无害的确认
        self.bag_reset_page()
        self.bag_sort()

    def bag_reset_page(self) -> bool:
        """
        重置背包页：点左侧「物品」页签（游戏设定：点当前模块会重置到第一个 tab）

        背包是长列表，停在当前滚动位置直接扫描会漏掉上面的物品；
        点一次当前页签游戏就会重新加载背包（回顶部、关掉详情面板）。
        注意：直接 `ui_goto(page_item_bag)` 在已经处于该页时是原地不动，不能用来重置。

        :return: 是否真的点了页签（主页面没有左侧栏时返回 False，只关一下面板）
        """
        logger.info('Reset bag page (click sidebar 物品)')
        if page_item_bag.module not in self.ui_sidebar_visible():
            logger.info('Sidebar is not visible, skip bag reset')
            self.bag_close_item_panel()
            return False
        if not self.ui_sidebar_click(page_item_bag.module):
            logger.warning('点击左侧「物品」页签失败，按当前页继续')
        self.device.sleep(self.BAG_SETTLE)
        self.bag_close_item_panel()
        return True

    def bag_scan_rule(self, rule, on_hit=None, max_screens: int = None,
                      empty_rule=None) -> bool:
        """
        逐屏模板匹配扫描背包（公共外壳）

        :param rule: RuleImage 模板（物品图标 + 第一行名字）
        :param on_hit: 命中回调 `on_hit(cell, image, score, hit)`：
                      cell = (列图标中心 x, 行文字区顶 y)，hit = 模板命中框 (x, y, w, h)；
                      返回 True 表示提前结束
        :param max_screens: 最多扫几屏，默认 BAG_SCAN_SCREENS
        :param empty_rule: 空格子特征（命中即到底），可不传
        :return: 是否至少命中一次
        """
        max_screens = max_screens or self.BAG_SCAN_SCREENS
        found = False
        last_signature = None
        for page in range(1, max_screens + 1):
            self.screenshot()
            image = self.device.image
            hits = rule.match_all_any(image, threshold=rule.threshold)
            page_cells = set()
            page_hit = False
            for score, x, y, w, h in sorted(hits, key=lambda hit: -hit[0]):
                cell = self.bag_cell_from_hit(x, y, w, h)
                if cell in page_cells:
                    continue
                page_cells.add(cell)
                page_hit = found = True
                if on_hit is not None and on_hit(cell, image, score, (x, y, w, h)):
                    return found
            if not page_hit:
                logger.info(f'背包第 {page} 屏未命中模板')

            if empty_rule is not None and self.appear(empty_rule):
                logger.info('本屏出现空物品格，停止翻页')
                break
            signature = self.bag_page_signature(image)
            if signature == last_signature:
                logger.info('背包没有继续滚动，停止翻页')
                break
            last_signature = signature
            self.bag_scroll()
        return found

    # ---------------------------------------------------------------- 找物品
    def bag_find_item(self, name: str, rule=None):
        """
        查找背包物品：**优先模板匹配**，没命中再退回 OCR 名字匹配

        :param name: 物品名（日志用；OCR 兜底时也拿它匹配）
        :param rule: 物品模板（RuleImage），不传就只走 OCR
        :return: (x, y) 物品图标中心；未找到返回 None
        """
        logger.hr('Find bag item', level=2)
        logger.attr('Bag item', name)
        self.bag_open_ready()

        if rule is not None:
            coord = self.bag_find_by_rule(rule, name)
            if coord is not None:
                return coord
            logger.info(f'模板未命中 [{name}]，重置背包页后走 OCR 名字匹配')
            self.bag_reset_page()
        return self.bag_find_by_ocr(name)

    def bag_find_by_rule(self, rule, name: str = ''):
        """
        模板匹配找物品，返回命中框对应的图标中心坐标；没命中返回 None

        点击坐标直接用**命中框**换算（`hit.x + 图标偏移, hit.y + hit.h / 2`）：
        行 top 那个常量是给「命中格 OCR 区域」用的，跟实际图标中心差 ~35px，
        拿来点会点在格子偏上的位置，边界上可能点错行。

        :param name: 物品名；非空时会对命中格再做一次 OCR 名字校验（防模板误命中）
        """
        hits = []

        def on_hit(cell, image, score, hit):
            x, y, w, h = hit
            if name:
                text_region, _ = self.bag_cell_regions(*cell)
                text = self.bag_region_text(text_region)
                if not OcrHelpers.ocr_name_match(text, name):
                    logger.warning(f'模板命中但格子文字对不上 [{name}]: {text!r} @ {cell}')
                    return False
            hits.append(((int(x + self.BAG_PICK_ICON_DX), int(y + h / 2)), score))
            return True  # 第一个有效命中就够了

        self.bag_scan_rule(rule, on_hit=on_hit)
        if not hits:
            logger.warning(f'Bag template [{name or rule.name}] not matched')
            return None
        coord, score = hits[0]
        logger.info(f'Bag item [{name or rule.name}] matched by template '
                    f'(score {score:.3f}) at {coord}')
        return coord

    def bag_find_by_ocr(self, name: str):
        """OCR 名字匹配找物品（模板没有/不命中时的兜底）"""
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

    def bag_click_item(self, name: str, rule=None) -> bool:
        """
        查找并点击背包物品
        :return: 是否点到物品
        """
        coord = self.bag_find_item(name, rule=rule)
        if coord is None:
            return False
        logger.info(f'Click bag item [{name}] at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name=f'bag_item_{name}')
        self.device.click_record_clear()
        return True

    def bag_use_item(self, name: str, rule=None) -> bool:
        """
        查找并点击背包物品，然后在详情面板点「立即使用」
        :return: 是否点到「立即使用」
        """
        if not self.bag_click_item(name, rule=rule):
            return False
        if not self.bag_wait_panel():
            logger.warning(f'Bag item [{name}] detail panel does not appear')
            return False
        return self.bag_panel_use()

    def bag_wait_panel(self, timeout: float = None) -> bool:
        """等物品详情面板出现（出现标志：面板上有「立即使用/全部使用/永久丢弃」）"""
        timeout = self.BAG_PANEL_TIMEOUT if timeout is None else float(timeout)
        timer = Timer(timeout).start()
        while 1:
            if self.bag_panel_open():
                return True
            if timer.reached():
                return False
            self.device.sleep(0.3)

    def bag_panel_open(self) -> bool:
        """物品详情面板是否开着（要最近一帧截图）"""
        self.screenshot()
        texts = [item.ocr_text for item in
                 self.O_BAG_GRID.detect_and_ocr(self.device.image, logDisplay=False)]
        return any(mark in text for text in texts for mark in self.BAG_PANEL_MARKS)

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

    # ---------------------------------------------------------------- 格子换算
    def bag_cell_from_hit(self, x, y, w, h):
        """
        模板命中框 -> 所在格子的 (列图标中心 x, 行文字区顶 y)

        模板是「图标 + 第一行名字」：框左边约在图标中心 -BAG_PICK_ICON_DX，
        框中心 y 约等于图标中心，换算后吸附到最近的行列。
        """
        icon_x = x + self.BAG_PICK_ICON_DX
        icon_y = y + h / 2
        col = min(self.BAG_COLUMN_CENTERS, key=lambda c: abs(c - icon_x))
        row_top = min(self.BAG_ROW_TOPS, key=lambda r: abs(r - (icon_y - self.BAG_PICK_ICON_HALF)))
        return col, row_top

    def bag_cell_center(self, col_x, row_top) -> tuple:
        """格子 -> 图标中心坐标（点击用）"""
        return int(col_x), int(row_top + self.BAG_PICK_ICON_HALF)

    def bag_cell_regions(self, col_x, row_top):
        """返回该物品格的 (文字区域, 数量区域)，格式 x,y,w,h"""
        text_region = (col_x + self.BAG_TEXT_DX, row_top,
                       self.BAG_TEXT_SIZE[0], self.BAG_TEXT_SIZE[1])
        count_region = (col_x + self.BAG_COUNT_DX, row_top + self.BAG_COUNT_DY,
                        self.BAG_COUNT_SIZE[0], self.BAG_COUNT_SIZE[1])
        return text_region, count_region

    def bag_region_text(self, region) -> str:
        """对指定区域做一次 OCR，返回按阅读顺序（先 y 后 x）拼接的文本"""
        rule = self.O_BAG_GRID
        old_roi = rule.roi
        try:
            rule.roi = region
            results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        finally:
            rule.roi = old_roi
        boxes = []
        for item in results:
            box = item.box
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            boxes.append((item.ocr_text.strip(),
                          (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        boxes.sort(key=lambda it: (round(it[2] / 20), it[1]))
        return ''.join(text for text, _, _ in boxes)

    def bag_region_digits(self, region) -> int:
        """
        读数量区域里的数字（小字，转灰度放大 2x/3x 再识别），读不到返回 0

        数量为 1 时游戏不显示数字，调用方把 0 当 1 处理。
        """
        x, y, w, h = region
        image = self.device.image
        if image is None:
            return 0
        crop = image[y:y + h, x:x + w]
        if crop is None or crop.size == 0:
            return 0
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        best = 0
        for scale in (2, 3):
            resized = cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), None,
                                 fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            result = self.O_BAG_GRID.model.ocr_single_line(resized)
            if not result:
                continue
            text, _ = result
            digits = re.sub(r'\D', '', str(text))
            if digits:
                best = max(best, int(digits))
        return best

    def bag_page_signature(self, image) -> bytes:
        """背包页的粗粒度指纹：滑动后内容没变就说明到底了（比整页 OCR 便宜）"""
        x, y, w, h = self.O_BAG_GRID.roi
        gray = cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY)
        return cv2.resize(gray, (64, 32), interpolation=cv2.INTER_AREA).tobytes()

    # ---------------------------------------------------------------- OCR 兜底
    def bag_item_scan(self, name: str):
        """
        在当前可见的背包格里按名字查找物品
        :return: ((x, y) 图标中心 / None, 当前可见物品名列表)
        """
        self.screenshot()
        results = self.O_BAG_GRID.detect_and_ocr(self.device.image, logDisplay=False)
        rows = self.parse_bag_items(results)
        names = [text for text, _, _ in rows]
        logger.attr('Bag rows', names)
        for text, x, y in rows:
            if not self.ocr_name_match(text, name):
                continue
            coord = self.bag_item_coord(x, y)
            logger.info(f'Bag item [{text}] found at {coord}')
            return coord, names
        return None, names

    def parse_bag_items(self, results: list) -> list:
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

    # ---------------------------------------------------------------- 背包操作
    def bag_close_item_panel(self) -> None:
        """
        关掉打开着的物品详情面板（面板会挡住背包格子并且不响应滚动）
        """
        if not self.bag_panel_open():
            return
        logger.info('Bag item panel is open, close it')
        self.device.click(x=self.BAG_PANEL_CLOSE[0], y=self.BAG_PANEL_CLOSE[1],
                          control_name='bag_panel_close')
        self.device.click_record_clear()
        self.device.sleep(self.BAG_SETTLE)

    def bag_sort(self) -> None:
        """点击整理背包，等整理动画结束后再开始找物品"""
        logger.info('Click sort bag')
        self.click(self.C_BAG_SORT)
        self.device.click_record_clear()
        self.device.sleep(self.BAG_SORT_SETTLE)

    def bag_scroll(self) -> None:
        """
        向下翻一整页（4 行）并等布局稳定

        用仙府的整页 swipe：整页滚动后行位置仍然是 110/228/350/471，
        命中框 -> 格子的换算才成立；改成小步滚动会让固定区域整体偏移。
        """
        self.device.swipe(p1=self.BAG_SCROLL_P1, p2=self.BAG_SCROLL_P2)
        self.device.click_record_clear()
        self.device.sleep(self.BAG_SCROLL_SETTLE)
