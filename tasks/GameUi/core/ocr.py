# This Python file uses the following encoding: utf-8
"""
OCR 通用能力（OcrHelpers）：

- 名称匹配（容忍形近字误识）：ocr_name_match / ocr_name_pick
- 彩色描边字读取：ocr_color_name（ROI 转灰度 -> 放大 -> 整行识别）
- 弹窗文案精确检测：dialog_appear

由 GameUi 混入本类使用。
"""
import difflib

import cv2
from module.atom.ocr import RuleOcr
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class OcrHelpers(BaseTask, GameUiAssets):
    # OCR 名称匹配相似度阈值（容忍形近字误识）
    OCR_NAME_SIMILARITY = 0.7

    @classmethod
    def ocr_name_match(cls, ocr_text: str, name: str) -> bool:
        """
        OCR 名称匹配：完全包含，或「前两字一致 + 相似度达标」（容忍 OCR 形近字误识）
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
        return difflib.SequenceMatcher(None, candidate, name).ratio() >= cls.OCR_NAME_SIMILARITY

    @classmethod
    def ocr_name_pick(cls, ocr_text: str, names: list) -> str | None:
        """
        在已知名称列表里为 OCR 文本挑出最接近的名称（容忍 OCR 形近字、丢字）

        名称列表都是固定的小集合，取相似度最高的一个；相似度不达标返回 None。
        用「取最接近」而不是「逐个包含判断」，避免 任务/任务特权 这类互相包含的名称误判。
        """
        if not ocr_text:
            return None
        best, best_score = None, 0.0
        for name in names:
            if not name:
                continue
            if ocr_text == name:
                return name
            window = ocr_text[:len(name)]
            ratio = difflib.SequenceMatcher(None, window, name).ratio()
            if name in ocr_text:
                ratio = max(ratio, 0.9)
            elif ocr_text in name and len(ocr_text) >= len(name) - 1:
                # 丢了 1 个字（如「物品」识别成「品」）
                ratio = max(ratio, 0.9)
            if ratio > best_score:
                best, best_score = name, ratio
        if best_score >= cls.OCR_NAME_SIMILARITY:
            return best
        return None

    def ocr_color_name(self, rule, scales: tuple = (2, 3), min_score: float = 0.5) -> str:
        """
        读取带描边的彩色文字（如锁定的目标名）

        规则默认的「检测框 + 拼串」在真机上经常一个框都检不出来（实测「妖化蟹将」-> 空串），
        或者把名字拆成两段各读错一个字（妖件 + 化蟹将）。
        这里改成：ROI 转灰度 -> 放大 2x/3x -> 整行识别，取置信度高的一份。

        :param rule: RuleOcr（使用它的 roi 与 ocr model）
        :param scales: 放大倍数
        :param min_score: 置信度下限，低于此值视为没读到
        :return: 识别到的文字，读不到返回空串
        """
        x, y, w, h = rule.roi
        image = self.device.image[y:y + h, x:x + w]
        if image is None or image.size == 0:
            return ''
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

        best_text, best_score, readings = '', 0.0, []
        for scale in scales:
            resized = cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_CUBIC)
            result = rule.model.ocr_single_line(resized)
            if not result:
                continue
            text, score = result
            score = float(score) if score is not None else 0.0
            if score != score:  # nan：模型没识别出任何字符
                score = 0.0
            readings.append((scale, text, round(score, 3)))
            if score > best_score:
                best_text, best_score = text, score

        if best_score < min_score:
            logger.warning(f'Color name not recognized: {readings}')
            return ''
        logger.attr('Color name', f'{best_text} ({best_score:.2f})')
        return best_text.strip()

    def dialog_appear(self, rule: RuleOcr, text: str) -> bool:
        """
        精确检测弹窗文案（不用 ocr_appear：框架的 OCR filter 有逐字符兜底匹配，会误判）
        :param rule: 弹窗文案的 OCR 规则（如 self.O_DIALOG_TEXT）
        :param text: 要匹配的文案片段
        """
        results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        return any(text in result.ocr_text for result in results)
