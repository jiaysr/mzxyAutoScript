# This Python file uses the following encoding: utf-8
"""
通用功能（GeneralFunctions）：

- 背包相关的通用操作，任务直接 `self.bag_xxx()` 调用，不要各自实现
- 后续其他通用功能（非背包）也放这里

目前实现：
- `bag_repair_equipment()` 修理装备：背包页 -> 随身功能 -> 修理装备
  -> 确认（文案中铜贝数量不固定，只匹配固定片段）-> 关修理成功提示 -> 回主页面
  -> 点修理后也可能直接弹「您没有装备需要修理」（无需修理），同样视为成功
"""
from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.pages.page import page_item_bag, page_main
from tasks.base_task import BaseTask


class GeneralFunctions(BaseTask, GameUiAssets):
    # 确认弹窗固定文案片段（铜贝数量不固定，不要匹配数字）
    BAG_REPAIR_CONFIRM_TEXTS = ('用于修理全部装备', '修理全部装备')
    # 修理成功提示文案
    BAG_REPAIR_SUCCESS_TEXT = '修理成功'
    # 无需修理提示文案（点修理后直接弹出，无确认弹窗）
    BAG_REPAIR_NOTHING_TEXT = '没有装备需要修理'
    # 随身功能菜单标题（4 按钮弹窗）
    BAG_GENERAL_MENU_TITLE = '请选择'
    # 修理装备按钮文案
    BAG_REPAIR_BUTTON_TEXT = '修理装备'
    # 随身功能按钮文案（背包底部）
    BAG_GENERAL_BUTTON_TEXT = '随身功能'

    def bag_repair_equipment(self) -> bool:
        """
        修理全部装备，成功返回 True
        """
        logger.hr('Repair equipment')
        if not self.ui_goto(page_item_bag, timeout=40):
            raise GameStuckError('Item bag page does not appear')
        self.device.sleep(0.5)

        if not self.bag_open_general_menu():
            logger.warning('General function menu does not appear')
            return False
        clicked = self.bag_click_repair()
        if not clicked:
            logger.warning('Repair button does not appear')
            self.bag_close_general_menu()
            return False
        if clicked == 'nothing':
            logger.info('No equipment needs repair')
            self.ui_goto(page_main, timeout=30)
            return True
        if not self.bag_confirm_repair():
            logger.warning('Repair confirm dialog does not appear')
            self.bag_close_general_menu()
            return False
        if not self.bag_close_repair_success():
            logger.warning('Repair success tip does not appear')
            return False

        logger.info('Repair equipment finished')
        self.ui_goto(page_main, timeout=30)
        return True

    # ------------------------------------------------------------------ 随身功能菜单
    def bag_open_general_menu(self, timeout: int = 10) -> bool:
        """
        点背包底部「随身功能」，等 4 按钮弹窗（标题「请选择」）出现
        """
        logger.hr('Open general function menu')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.bag_general_menu_appear():
                logger.info('General function menu appeared')
                return True
            if not self.bag_click_general_button():
                self.device.sleep(0.5)
                continue
            self.device.sleep(0.8)
        return False

    def bag_click_general_button(self) -> bool:
        """
        点击「随身功能」按钮：OCR 定位精确点击，找不到时用固定坐标兜底
        （注意：appear_then_click 只接受 RuleImage/RuleOcr，不能传 RuleClick）
        """
        self.screenshot()
        coord = self.bag_menu_text_coord(self.O_BAG_BOTTOM, self.BAG_GENERAL_BUTTON_TEXT)
        if coord is None:
            coord = self.C_BAG_GENERAL_FUNCTION.coord()
            logger.info(f'Click general function button at {coord} (fixed)')
        else:
            logger.info(f'Click general function button at {coord} (ocr)')
        self.device.click(x=coord[0], y=coord[1], control_name='bag_general_function')
        self.device.click_record_clear()
        return True

    def bag_general_menu_appear(self) -> bool:
        """
        4 按钮弹窗是否出现（标题「请选择」+「修理装备」按钮都在）
        """
        self.maybe_screenshot()
        results = self.O_BAG_GENERAL_MENU.detect_and_ocr(self.device.image, logDisplay=False)
        texts = [item.ocr_text.strip() for item in results]
        has_title = any(self.BAG_GENERAL_MENU_TITLE in text for text in texts)
        has_repair = any(self.BAG_REPAIR_BUTTON_TEXT in text for text in texts)
        return has_title and has_repair

    def bag_close_general_menu(self) -> None:
        """
        关掉 4 按钮弹窗（右上角绿 X），异常路径下调用，避免盖住后续操作
        """
        timer = Timer(5).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if not self.bag_general_menu_appear():
                return
            logger.info('Close general function menu')
            self.appear_then_click(self.I_CLOSE_X_GREEN_SMALL, interval=1)
            self.appear_then_click(self.I_CLOSE_X_GREEN, interval=1)
            self.device.click_record_clear()
            self.device.sleep(0.5)

    # ------------------------------------------------------------------ 修理确认
    def bag_click_repair(self, timeout: int = 10):
        """
        在 4 按钮弹窗里点「修理装备」，等修理确认弹窗出现
        :return: 'confirm' = 弹出确认框待点确定；'nothing' = 无需修理（已关提示）；
                 False = 修理按钮没出现
        """
        logger.hr('Click repair equipment')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.bag_repair_confirm_appear():
                logger.info('Repair confirm dialog appeared')
                return 'confirm'
            if self.bag_repair_nothing_appear():
                logger.info('No equipment needs repair')
                self.bag_close_repair_nothing()
                return 'nothing'
            coord = self.bag_menu_text_coord(self.O_BAG_GENERAL_MENU, self.BAG_REPAIR_BUTTON_TEXT)
            if coord:
                logger.info(f'Click repair button at {coord}')
                self.device.click(x=coord[0], y=coord[1], control_name='bag_repair')
                self.device.click_record_clear()
            self.device.sleep(0.8)
        return False

    def bag_repair_confirm_appear(self) -> bool:
        """
        修理确认弹窗是否出现（只匹配固定文案片段，铜贝数量不固定所以不匹配数字）
        """
        self.maybe_screenshot()
        results = self.O_BAG_REPAIR_CONFIRM.detect_and_ocr(self.device.image, logDisplay=False)
        return any(any(key in item.ocr_text for key in self.BAG_REPAIR_CONFIRM_TEXTS)
                   for item in results)

    def bag_confirm_repair(self, timeout: int = 10) -> bool:
        """
        在确认弹窗里点「确定」，等「修理成功」提示出现
        """
        logger.hr('Confirm repair')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.bag_repair_success_appear():
                logger.info('Repair success appeared')
                return True
            if not self.bag_repair_confirm_appear():
                self.device.sleep(0.5)
                continue
            coord = self.bag_menu_text_coord(self.O_BAG_REPAIR_CONFIRM, '确定')
            if coord:
                logger.info(f'Click repair confirm at {coord}')
                self.device.click(x=coord[0], y=coord[1], control_name='bag_repair_confirm')
                self.device.click_record_clear()
            self.device.sleep(0.8)
        return False

    # ------------------------------------------------------------------ 修理成功
    def bag_repair_success_appear(self) -> bool:
        """
        「修理成功」提示是否出现
        """
        self.maybe_screenshot()
        results = self.O_BAG_REPAIR_RESULT.detect_and_ocr(self.device.image, logDisplay=False)
        return any(self.BAG_REPAIR_SUCCESS_TEXT in item.ocr_text for item in results)

    def bag_repair_nothing_appear(self) -> bool:
        """
        「您没有装备需要修理」提示是否出现（与修理成功是同一样式的提示条）
        """
        self.maybe_screenshot()
        results = self.O_BAG_REPAIR_RESULT.detect_and_ocr(self.device.image, logDisplay=False)
        return any(self.BAG_REPAIR_NOTHING_TEXT in item.ocr_text for item in results)

    def _bag_close_tip(self, appear, label: str, timeout: int = 10) -> bool:
        """
        关掉提示条（右上角绿 X），直到提示消失
        """
        timer = Timer(timeout).start()
        seen = False
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if not appear():
                if seen:
                    logger.info(f'{label} tip closed')
                    return True
                self.device.sleep(0.5)
                continue
            seen = True
            logger.info(f'Close {label} tip')
            self.appear_then_click(self.I_CLOSE_X_GREEN_SMALL, interval=1)
            self.appear_then_click(self.I_CLOSE_X_GREEN, interval=1)
            self.device.click_record_clear()
            self.device.sleep(0.5)
        return seen

    def bag_close_repair_success(self, timeout: int = 10) -> bool:
        """
        关掉「修理成功」提示（右上角绿 X），直到提示消失
        """
        logger.hr('Close repair success tip')
        return self._bag_close_tip(self.bag_repair_success_appear, 'repair success', timeout)

    def bag_close_repair_nothing(self, timeout: int = 10) -> bool:
        """
        关掉「您没有装备需要修理」提示（右上角绿 X），直到提示消失
        """
        logger.hr('Close repair nothing tip')
        return self._bag_close_tip(self.bag_repair_nothing_appear, 'repair nothing', timeout)

    # ------------------------------------------------------------------ OCR 点击
    def bag_menu_text_coord(self, rule, text: str) -> tuple | None:
        """
        在指定 OCR 区域里找包含 text 的文本框，返回可点击中心坐标
        :param rule: OCR 规则（用它的 roi）
        :param text: 要匹配的文案片段
        :return: (x, y) / None
        """
        roi = rule.roi
        for item in rule.detect_and_ocr(self.device.image, logDisplay=False):
            ocr_text = item.ocr_text.strip()
            if not ocr_text or text not in ocr_text:
                if not self.ocr_name_match(ocr_text, text):
                    continue
            box = item.box
            x = int((min(point[0] for point in box) + max(point[0] for point in box)) / 2) + roi[0]
            y = int((min(point[1] for point in box) + max(point[1] for point in box)) / 2) + roi[1]
            return x, y
        return None

