# This Python file uses the following encoding: utf-8
"""
银贝礼盒（SilverGiftBox）：每天一次，到牧野的宝具商人处兑换银贝礼盒

礼盒每天凌晨 4 点刷新。当前只实现走到 NPC 处：
传送牧野 -> 开小地图 -> 右侧 NPC 列表点宝具商人（人物自动寻路）-> 等坐标稳定
NPC 对话弹窗后的兑换操作待后续补充。
"""
from datetime import datetime, time, timedelta

import re

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                               GamePageUnknownError,
                               GameStuckError,
                               TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.SilverGiftBox.assets import SilverGiftBoxAssets


class ScriptTask(GameUi, SilverGiftBoxAssets):
    SCHEDULER_NAME = 'SilverGiftBox'

    # 礼盒每天凌晨 4 点刷新
    REFRESH_HOUR = 4

    def run(self) -> None:
        cfg = self.config.silver_gift_box.silver_gift_box_config
        logger.hr(f'银贝礼盒 npc={cfg.npc_name} 地图={cfg.relay_location}')
        try:
            self.prepare_game()
            location = self.goto_npc(cfg)
            logger.attr('已走到 NPC 处', f'[{location[0]}] {location[1]},{location[2]}')
            if not self.open_exchange_page(cfg):
                logger.warning('Exchange page does not appear')
            elif not self.select_gift():
                logger.warning('Silver gift box not verified')
            else:
                max_n = self.buy_prepare()
                if not max_n:
                    logger.warning('Buy prepare failed')
                elif not self.buy_confirm(count=max_n):
                    logger.warning('Buy confirm failed')
                else:
                    logger.info(f'购买 {max_n} 个完成')
        finally:
            # 正常/异常都要重排到下一个凌晨 4 点，避免 next_run 停在过去一直霸占调度
            self.schedule_next_run()
        raise TaskEnd('SilverGiftBox')

    # ---------------------------------------------------------------- 准备
    def prepare_game(self) -> None:
        """确保游戏在线并停留在主页面"""
        logger.hr('准备游戏（在线 + 主页面）')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            logger.info('游戏已登录到主页面')
            return
        try:
            from tasks.GameUi.pages.page import page_main
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=60):
                raise GameStuckError('Main page does not appear')
            logger.info('游戏在线且在主页面')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError):
            logger.warning('无法切回主页面，重启游戏')
            self.ui_restart_game()

    # ---------------------------------------------------------------- 走到 NPC
    def clear_blocking_popups(self, max_close: int = 3) -> None:
        """
        关掉挡住小地图入口的残留弹窗（如 NPC 对话框、活动弹窗）

        模态弹窗会吞掉小地图入口的点击：连点 10 次打不开小地图就会触发连点保护
        （实测：上古狩猎神对话弹窗开着时入口点击全部无效）。
        """
        logger.hr('Clear blocking popups')
        for _ in range(max_close):
            self.reset_records()
            if not self.try_close_unknown_page(skip_screenshot=False):
                break
            self.device.sleep(0.5)
        self.map_close_main_popup()

    def goto_npc(self, cfg) -> tuple:
        """
        传送到 NPC 所在地图 -> 小地图点 NPC -> 等自动寻路走完
        :return: 到达的位置 (地点名, x, y)
        """
        logger.hr(f'前往 NPC [{cfg.npc_name}] @ {cfg.relay_location}')
        self.clear_blocking_popups()
        # 已经在 NPC 地图就不用传送：世界地图列表里不包含当前所在地点，
        # 已经在牧野时再调 map_teleport('牧野') 必然找不到目标（map.py 的已知行为）
        location = self.map_move_read_pos()
        if location is not None and self.map_name_match(location[0], cfg.relay_location):
            logger.attr('Relay location', f'Already at [{location[0]}] {location[1]},{location[2]}, skip teleport')
        elif not self.map_teleport(cfg.relay_location):
            raise GameStuckError(f'Cannot teleport to [{cfg.relay_location}]')
        if not self.map_open_minimap_and_click_npc(cfg.npc_name):
            raise GameStuckError(f'NPC [{cfg.npc_name}] not found in minimap')
        location = self.map_wait_walk_arrive(stable_time=int(cfg.arrive_stable),
                                             timeout=int(cfg.arrive_timeout))
        if location is None:
            raise GameStuckError(f'Did not walk to NPC [{cfg.npc_name}]')
        return location

    # ---------------------------------------------------------------- 兑换页
    # 对话框列表无变化连续几次就认为到底，不再滚动
    DIALOG_SCROLL_STALL = 2

    def wait_npc_dialog(self, cfg, timeout: int = 15) -> bool:
        """
        等 NPC 对话框弹出（走到 NPC 后自动弹出）

        用常驻的「确定」按钮判定，不用标题/选项名：标题在识别区之外，
        目标选项（如玲珑换宝）可能要滚屏才看得到。
        """
        logger.hr('Wait NPC dialog')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.dialog_text_coord(self.O_SILVER_DIALOG, '确定'):
                logger.info('NPC dialog appeared')
                return True
            self.device.sleep(0.5)
        logger.warning('NPC dialog does not appear')
        return False

    def dialog_text_coord(self, rule, text: str) -> tuple | None:
        """在对话框区域找包含 text 的文本框，返回可点击中心坐标"""
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

    def dialog_signature(self) -> tuple:
        """对话框当前可见文本签名，判断滚动是否还有效"""
        return tuple(sorted(item.ocr_text.strip() for item in
                            self.O_SILVER_DIALOG.detect_and_ocr(self.device.image, logDisplay=False)))

    def dialog_scroll_up(self) -> None:
        """对话框选项列表往上翻一屏（找下面的选项）"""
        x0, y0, w, h = self.O_SILVER_DIALOG.roi
        center_x = x0 + w // 2
        top, bottom = y0 + 60, y0 + h - 80
        self.ui_swipe_gentle((center_x, bottom), (center_x, top), steps=3, step_delay=0.2)
        self.device.click_record_clear()
        self.device.sleep(0.8)

    def exchange_page_appear(self) -> bool:
        """玲珑换宝页是否打开（标题出现即算）"""
        self.maybe_screenshot()
        results = self.O_EXCHANGE_TITLE.detect_and_ocr(self.device.image, logDisplay=False)
        return any('玲珑换宝' in item.ocr_text for item in results)

    def open_exchange_page(self, cfg, timeout: int = 40) -> bool:
        """
        NPC 对话框里找兑换选项 -> 点击选中 -> 点确定 -> 等玲珑换宝页
        """
        logger.hr(f'Open exchange [{cfg.shop_option}]')
        if not self.wait_npc_dialog(cfg):
            return False
        timer = Timer(timeout).start()
        stall = 0
        last_signature = None
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.exchange_page_appear():
                logger.info('Exchange page appeared')
                return True
            coord = self.dialog_text_coord(self.O_SILVER_DIALOG, cfg.shop_option)
            if coord is None:
                signature = self.dialog_signature()
                if signature == last_signature:
                    stall += 1
                    if stall >= self.DIALOG_SCROLL_STALL:
                        logger.warning(f'Exchange option [{cfg.shop_option}] not found, list bottom reached')
                        return False
                else:
                    stall = 0
                last_signature = signature
                logger.info(f'Exchange option [{cfg.shop_option}] not visible, scroll up')
                self.dialog_scroll_up()
                continue
            logger.info(f'Click exchange option at {coord}')
            self.device.click(x=coord[0], y=coord[1], control_name='silver_exchange_option')
            self.device.click_record_clear()
            self.device.sleep(0.5)
            confirm = self.dialog_text_coord(self.O_SILVER_DIALOG, '确定')
            if confirm:
                logger.info(f'Click exchange confirm at {confirm}')
                self.device.click(x=confirm[0], y=confirm[1], control_name='silver_exchange_confirm')
                self.device.click_record_clear()
            self.device.sleep(1.0)
        logger.warning('Exchange page does not appear')
        return False

    # ---------------------------------------------------------------- 选礼盒
    # 兑换物品网格几何（1280x720，真机实测）：
    # 第一行 12 个，第二行若干个；列中心 x = 116 + i*87，行中心 y = 148 / 235
    EXCHANGE_ROW1_Y = 148
    EXCHANGE_COL_X0 = 116
    EXCHANGE_COL_PITCH = 87
    EXCHANGE_COLS = 12

    def exchange_row1_last_coord(self) -> tuple:
        """第一行最后一个物品中心"""
        x = self.EXCHANGE_COL_X0 + (self.EXCHANGE_COLS - 1) * self.EXCHANGE_COL_PITCH
        return int(x), self.EXCHANGE_ROW1_Y

    def exchange_find_gift(self):
        """
        第二行从后往前找礼盒模板，返回最右侧命中的中心坐标
        :return: (x, y) / None
        """
        self.screenshot()
        matches = self.I_GIFT_BOX.match_all(self.device.image)
        if not matches:
            return None
        matches.sort(key=lambda m: -m[1])
        score, x, y, w, h = matches[0]
        logger.attr('Gift match', f'score={score:.3f} at ({x},{y})')
        return int(x + w / 2), int(y + h / 2)

    def gift_is_silver(self) -> bool:
        """选中后的物品提示框是否为银贝礼盒（名字行图片匹配）"""
        self.screenshot()
        return self.appear(self.I_GIFT_NAME_SILVER)

    # ---------------------------------------------------------------- 购买准备（到选数量，不点确定）
    # 数量弹窗减号按钮固定坐标（1280x720，真机验证有效）：
    # 大减号（-10）在数字下方，小减号（-1）在数字左侧，加号（+1）在数字右侧
    MINUS_BIG = (640, 387)
    MINUS_SMALL = (597, 322)

    def buy_option_appear(self) -> bool:
        """请选择弹窗（查看/购买）是否出现"""
        self.maybe_screenshot()
        results = self.O_BUY_OPTION.detect_and_ocr(self.device.image, logDisplay=False)
        return any('请选择' in item.ocr_text for item in results)

    def quantity_appear(self) -> bool:
        """选择数量弹窗是否出现"""
        self.maybe_screenshot()
        results = self.O_BUY_QUANTITY.detect_and_ocr(self.device.image, logDisplay=False)
        return any('选择数量' in item.ocr_text for item in results)

    def quantity_max(self):
        """从标题 选择数量(1-4) 解析购买上限"""
        self.screenshot()
        for item in self.O_BUY_QUANTITY.detect_and_ocr(self.device.image, logDisplay=False):
            match = re.search(r'[（(](\d+)\s*[-~]\s*(\d+)[）)]', item.ocr_text)
            if match:
                return int(match.group(2))
        return None

    def quantity_current(self):
        """
        读选择数量弹窗中间的当前数量

        新 OCR 引擎（RapidOCR + PP-OCRv6）对白色小数字稳定可读：
        真机实测「选择数量(1-1)」弹窗连点减号 14 次，读数全部正确，
        数字 1 不会再被误读成 7（旧 ppocr-onnx 引擎 det 路径会把 1 读成 7）。
        :return: 当前数量 / None（没读出来）
        """
        self.screenshot()
        texts = [item.ocr_text for item in
                 self.O_BUY_QUANTITY_NUM.detect_and_ocr(self.device.image, logDisplay=False)]
        for text in texts:
            match = re.search(r'\d+', text)
            if match:
                return int(match.group(0))
        logger.info(f'Quantity texts: {texts}')
        return None

    def click_minus(self, coord: tuple, delay: float = 1.0) -> None:
        """点一次减号并等待数字刷新"""
        self.device.click(x=coord[0], y=coord[1], control_name='buy_quantity_minus')
        self.device.click_record_clear()
        self.device.sleep(delay)

    def quantity_set(self, target: int, max_n: int, timeout: int = 20) -> bool:
        """
        用减号把数量调到 target：每一步都读数为准，读到目标才收工

        数量在 1 时点减号会回绕到 max（真机验证），所以差值按 (current - target) % max_n 算，
        大减号(-10) 拆 10 的倍数、小减号(-1) 收尾；读不到数量直接放弃，避免买错数量。
        :return: 是否调到位
        """
        logger.hr(f'Set quantity to {target}')
        if max_n < 1:
            logger.warning(f'Invalid max {max_n}')
            return False
        timer = Timer(timeout).start()
        while not timer.reached():
            current = self.quantity_current()
            if current is None:
                logger.warning('Quantity unreadable, abort')
                return False
            if current == target:
                logger.attr('Quantity', current)
                return True
            if current > max_n:
                logger.warning(f'Quantity {current} out of range (max {max_n})')
                return False

            diff = (current - target) % max_n
            big, small = divmod(diff, 10)
            logger.info(f'Quantity {current} -> {target}: -10 x{big}, -1 x{small}')
            for _ in range(big):
                self.click_minus(self.MINUS_BIG)
            for _ in range(small):
                self.click_minus(self.MINUS_SMALL)
        logger.warning(f'Set quantity to {target} timeout')
        return False

    def quantity_set_max(self, max_n: int, timeout: int = 15) -> bool:
        """
        点减号把数量调到最大：在 1 点减号会回绕到最大值。

        读数闭环：每次都读当前数量，直到读到 max_n（读不到就失败，不做猜测）。
        """
        logger.hr(f'Set quantity to max {max_n}')
        return self.quantity_set(max_n, max_n, timeout=timeout)

    def buy_prepare(self):
        """
        再点一次礼盒 -> 请选择点购买 -> 数量弹窗调到最大（不点确定）
        :return: 购买上限 / None（失败）
        """
        logger.hr('Prepare buy gift')
        coord = self.exchange_find_gift()
        if coord is None:
            logger.warning('Gift box not found for second click')
            return False
        logger.info(f'Click gift box again at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='exchange_gift_again')
        self.device.click_record_clear()

        timer = Timer(10).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.buy_option_appear():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('Buy option popup does not appear')
            return False

        buy = self.dialog_text_coord(self.O_BUY_OPTION, '购买')
        if not buy:
            logger.warning('Buy button not found')
            return False
        logger.info(f'Click buy at {buy}')
        self.device.click(x=buy[0], y=buy[1], control_name='exchange_buy')
        self.device.click_record_clear()

        timer = Timer(10).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.quantity_appear():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('Quantity popup does not appear')
            return False

        max_n = self.quantity_max()
        if max_n is None:
            logger.warning('Quantity max not recognized')
            return False
        logger.attr('Quantity max', max_n)
        if not self.quantity_set_max(max_n):
            return None
        return max_n

    def select_gift(self) -> bool:
        """
        点第一行最后一个（把物品提示框挪开，不遮挡第二行）
        -> 第二行从后往前找礼盒模板并点击 -> 校验提示框是否为银贝礼盒
        """
        logger.hr('Select silver gift box')
        x, y = self.exchange_row1_last_coord()
        logger.info(f'Click row1 last item at ({x},{y}) to move tooltip away')
        self.device.click(x=x, y=y, control_name='exchange_row1_last')
        self.device.click_record_clear()
        self.device.sleep(0.8)

        coord = self.exchange_find_gift()
        if coord is None:
            logger.warning('Gift box not found in row 2')
            return False
        logger.info(f'Click gift box at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='exchange_gift')
        self.device.click_record_clear()
        self.device.sleep(0.8)

        if not self.gift_is_silver():
            logger.warning('Selected item is not 银贝礼盒')
            return False
        logger.info('Gift verified: 银贝礼盒')
        return True

    # ---------------------------------------------------------------- 购买确认
    def dialog_confirm_coord(self) -> tuple | None:
        """数量弹窗里的确定按钮坐标"""
        self.screenshot()
        return self.dialog_text_coord(self.O_BUY_QUANTITY, '确定')

    def buy_confirm(self, count: int = 1, timeout: int = 30) -> bool:
        """
        数量调到 count -> 点确定购买 -> 关购买结果弹窗 -> 关兑换页回主页面

        调数量以 OCR 读数为准（RapidOCR + PP-OCRv6 对白色小数字稳定，1 不再误读成 7）：
        先读当前数量，再按差值点减号并逐步校验，读不到数量就放弃，避免买错数量。
        """
        logger.hr(f'Confirm buy {count}')
        max_n = self.quantity_max()
        if max_n is None:
            logger.warning('Quantity max not recognized')
            return False
        if not 1 <= count <= max_n:
            logger.warning(f'Buy count {count} out of range (1-{max_n})')
            return False
        if not self.quantity_set(count, max_n):
            logger.warning('Quantity not ready, abort buy')
            return False

        coord = self.dialog_confirm_coord()
        if not coord:
            logger.warning('Buy confirm button not found')
            return False
        logger.info(f'Click buy confirm at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='buy_confirm')
        self.device.click_record_clear()

        if not self.buy_close_result():
            logger.warning('Buy result popup not handled')
            return False
        return self.buy_back_to_main()

    def quantity_gone(self) -> bool:
        """数量弹窗是否已消失（购买已提交）：只认 选择数量 标题"""
        self.maybe_screenshot()
        results = self.O_BUY_QUANTITY.detect_and_ocr(self.device.image, logDisplay=False)
        texts = [item.ocr_text for item in results]
        return not any('选择数量' in text for text in texts)

    def buy_close_result(self, timeout: int = 20) -> bool:
        """
        关购买成功的买好了提示条：等数量弹窗消失（购买已提交）
        -> 等买好了出现 -> 点右上绿 X -> 等提示消失
        """
        logger.hr('Close buy result')
        timer = Timer(15).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.quantity_gone():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('Quantity popup still open')
            return False
        seen = False
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_BUY_SUCCESS):
                seen = True
                logger.info('Buy success tip appeared')
                self.appear_then_click(self.I_CLOSE_X_GREEN_SMALL, interval=1)
                self.appear_then_click(self.I_CLOSE_X_GREEN, interval=1)
                self.device.click_record_clear()
            elif seen:
                logger.info('Buy success tip closed')
                return True
            self.device.sleep(0.5)
        if not seen:
            logger.warning('Buy success tip does not appear')
        return False

    def buy_back_to_main(self, timeout: int = 30) -> bool:
        """关兑换页（右上返回）回主页面"""
        logger.hr('Back to main')
        from tasks.GameUi.pages.page import page_main
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.ui_page_appear(page_main):
                logger.info('Back on main page')
                self.ui_reset_current_page()
                return True
            coord = self.dialog_text_coord(self.O_PAGE_BACK, '返回')
            if coord:
                logger.info(f'Click page back at {coord}')
                self.device.click(x=coord[0], y=coord[1], control_name='exchange_back')
                self.device.click_record_clear()
            self.device.sleep(1.0)
        logger.warning('Back to main failed')
        return False

    # ---------------------------------------------------------------- 排期
    def schedule_next_run(self) -> None:
        """
        排到下一个凌晨 4 点（礼盒刷新时间）。

        server=False：不走服务端刷新时间——那会把 next_run 强行覆盖成
        「每天固定 server_update 那一刻」（见 module/config/config.py 的 task_delay）。
        """
        now = datetime.now()
        target = datetime.combine(now.date(), time(self.REFRESH_HOUR, 0))
        if target <= now:
            target += timedelta(days=1)
        logger.attr('下次运行', target.strftime('%Y-%m-%d %H:%M:%S'))
        self.set_next_run(task='SilverGiftBox', target=target, success=None, finish=True, server=False)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
