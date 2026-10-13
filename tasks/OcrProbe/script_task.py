# This Python file uses the following encoding: utf-8
"""
OCR 探针：数量弹窗读数测试（不点确定）

复用银贝礼盒的兑换流程走到「选择数量」弹窗，然后：
1. 用多种方案读取中间的数量（规则 / 引擎原始 det+rec / 纯识别 / 颜色过滤）
2. 点一次 -1（小减号）后再读，连续 N 次
3. 全程不点确定、不产生购买

用于验证新 OCR 引擎（RapidOCR + PP-OCRv6）对小号白色数字的识别效果。
截图存到 log/mcp/runs/ocr_probe/ 便于事后核对。
"""
import os
import re

import cv2
import numpy as np

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger
from module.ocr.base_ocr import enlarge_canvas
from module.ocr.models import get_local_ocr_model
from module.ocr.utils import ColorFilterConfig

from tasks.SilverGiftBox.script_task import ScriptTask as SilverScriptTask

SAVE_DIR = os.path.join('log', 'mcp', 'runs', 'ocr_probe')


def digits_of(texts) -> str:
    """把识别结果里的数字提出来，便于一眼比对"""
    out = []
    for text in texts:
        match = re.search(r'\d+', str(text))
        out.append(match.group(0) if match else '?')
    return ','.join(out) if out else '-'


class ScriptTask(SilverScriptTask):
    CLICKS = 14          # 点减号次数
    STEP_DELAY = 0.9     # 每次点击后的等待

    def run(self) -> None:
        logger.hr('OCR 探针：数量弹窗')
        try:
            self.enter_quantity_dialog()
            if not self.quantity_appear():
                logger.warning('数量弹窗没有打开：请手动打开「选择数量」弹窗后重跑本任务')
                raise TaskEnd('OcrProbe')
            self.probe_quantity()
        finally:
            logger.info('探针结束（未点确定，数量弹窗保持打开）')
        raise TaskEnd('OcrProbe')

    # ------------------------------------------------------------ 走到数量弹窗
    def enter_quantity_dialog(self) -> None:
        self.screenshot()
        if self.quantity_appear():
            logger.info('数量弹窗已在屏幕上')
            return

        cfg = self.config.silver_gift_box.silver_gift_box_config

        # 关掉挡路的聊天窗（右上角绿 X）
        for _ in range(3):
            self.screenshot()
            if self.appear_then_click(self.I_CLOSE_X_GREEN, interval=0):
                logger.info('关闭聊天窗（绿 X）')
                self.device.sleep(0.8)
                continue
            if self.appear_then_click(self.I_CLOSE_X_GREEN_SMALL, interval=0):
                logger.info('关闭聊天窗（小绿 X）')
                self.device.sleep(0.8)
                continue
            break

        # 确保 NPC 对话框弹出：没弹出就重新从小地图点 NPC 走过去（任务同款路径）
        self.screenshot()
        if not self.dialog_text_coord(self.O_SILVER_DIALOG, '确定'):
            logger.info('NPC 对话框未打开，重新点小地图 NPC')
            if not self.map_open_minimap_and_click_npc(cfg.npc_name):
                logger.warning('小地图里找不到 NPC，退出')
                return
            self.map_wait_walk_arrive(stable_time=int(cfg.arrive_stable),
                                      timeout=int(cfg.arrive_timeout))
            if not self.wait_npc_dialog(cfg, timeout=15):
                logger.warning('NPC 对话框还是没出现，退出')
                return

        if not self.open_exchange_page(cfg):
            logger.warning('打不开玲珑换宝页，退出')
            return

        if not self.select_gift():
            logger.warning('没选中银贝礼盒，退出')
            return

        # 再点一次礼盒 -> 购买 -> 数量弹窗（照抄 buy_prepare，但不点减号）
        coord = self.exchange_find_gift()
        if coord is None:
            logger.warning('第二次找不到礼盒，退出')
            return
        logger.info(f'再次点击礼盒 {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='probe_gift_again')
        self.device.click_record_clear()

        timer = Timer(10).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.buy_option_appear():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('请选择弹窗未出现，退出')
            return

        buy = self.dialog_text_coord(self.O_BUY_OPTION, '购买')
        if not buy:
            logger.warning('购买按钮未找到，退出')
            return
        logger.info(f'点击购买 {buy}')
        self.device.click(x=buy[0], y=buy[1], control_name='probe_buy')
        self.device.click_record_clear()

        timer = Timer(10).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.quantity_appear():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('数量弹窗未出现，退出')

    # ------------------------------------------------------------ 读数 + 点减号
    def probe_quantity(self) -> None:
        os.makedirs(SAVE_DIR, exist_ok=True)
        max_n = self.quantity_max()
        logger.attr('数量上限', max_n)

        self.read_quantity('step00')
        for step in range(1, self.CLICKS + 1):
            logger.info(f'--- 点击减号（第 {step} 次）---')
            self.device.click(x=self.MINUS_SMALL[0], y=self.MINUS_SMALL[1], control_name='probe_minus')
            self.device.click_record_clear()
            self.device.sleep(self.STEP_DELAY)
            self.read_quantity(f'step{step:02d}')

    def read_quantity(self, tag: str) -> None:
        """用多种方案读当前数量并打日志"""
        self.screenshot()
        image = self.device.image
        x, y, w, h = self.O_BUY_QUANTITY_NUM.roi

        # 1) 规则路径（带 enlarge_canvas + 帧缓存）
        rule_results = self.O_BUY_QUANTITY_NUM.detect_and_ocr(image, logDisplay=False)
        rule_texts = [item.ocr_text for item in rule_results]
        rule_scores = [round(float(item.score), 2) for item in rule_results]

        # 2) 引擎原始路径
        model = get_local_ocr_model()
        crop = image[y:y + h, x:x + w]
        cv2.imwrite(os.path.join(SAVE_DIR, f'{tag}.png'), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))

        raw_results = model.detect_and_ocr(enlarge_canvas(crop), drop_score=0.0)
        raw_texts = [item.ocr_text for item in raw_results]

        # 3) 纯识别
        single_text, single_score = model.ocr_single_line(crop)

        # 4) 颜色过滤后纯识别（白字）
        white = ColorFilterConfig(method='HSV', ranges=[((0, 0, 170), (180, 70, 255))])
        color_text, color_score = model.ocr_single_line(white.apply(crop))

        # 5) 外扩一点的 ROI（数字可能贴边）
        pad = 10
        px, py = max(0, x - pad), max(0, y - pad)
        pw, ph = w + pad * 2, h + pad * 2
        padded = image[py:py + ph, px:px + pw]
        pad_results = model.detect_and_ocr(enlarge_canvas(padded), drop_score=0.0)
        pad_texts = [item.ocr_text for item in pad_results]

        logger.info(
            f'[probe {tag}] rule={list(zip(rule_texts, rule_scores))} raw={raw_texts} '
            f'single=({single_text!r},{single_score:.2f}) color=({color_text!r},{color_score:.2f}) pad={pad_texts}'
        )
        logger.attr(f'读数 {tag}', f'rule={digits_of(rule_texts)} raw={digits_of(raw_texts)} '
                                    f'single={digits_of([single_text])} color={digits_of([color_text])} '
                                    f'pad={digits_of(pad_texts)}')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
