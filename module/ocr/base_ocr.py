# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import cv2
import numpy as np

from enum import Enum


from module.base.decorator import cached_property
from module.base.utils import area_pad, crop, float2str
from typing import Any

from module.ocr.batch import FrameOcrCache, shift_box
from module.ocr.engine import BoxedResult
from module.ocr.models import get_ocr_model
from module.ocr.utils import ColorFilterConfig
from module.exception import ScriptError
from module.logger import logger



def enlarge_canvas(image):
    """
    copy from https://github.com/LmeSzinc/StarRailCopilot
    Enlarge image into a square fill with black background. In the structure of PaddleOCR,
    image with w:h=1:1 is the best while 3:1 rectangles takes three times as long.
    Also enlarge into the integer multiple of 32 cause PaddleOCR will downscale images to 1/32.
    """
    height, width = image.shape[:2]
    length = int(max(width, height) // 32 * 32 + 32)
    border = (0, length - height, 0, length - width)
    if sum(border) > 0:
        image = cv2.copyMakeBorder(image, *border, borderType=cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return image


class OcrMode(Enum):
    FULL = 1  # str: "Full"
    SINGLE = 2  # str: "Single"
    DIGIT = 3  # str: "Digit"
    DIGITCOUNTER = 4  # str: "DigitCounter"
    DURATION = 5  # str: "Duration"
    QUANTITY = 6  # str: "Quantity"

class OcrMethod(Enum):
    DEFAULT = 1  # str: "Default"

class BaseCor:

    lang: str = "ch"
    score: float = 0.6  # 阈值默认为0.5
    min_score: float = 0.3  # 宽松阈值，用于挽救数字等结果

    name: str = "ocr"
    mode: OcrMode = OcrMode.FULL
    method: OcrMethod = OcrMethod.DEFAULT  # 占位符
    roi: list = []  # [x, y, width, height]
    area: list = []  # [x, y, width, height]
    keyword: str = ""  # 默认为空
    color_filter: ColorFilterConfig = None  # 颜色过滤，识别前先二值化
    cache_disable: bool = False  # 置 true 时不使用帧缓存（每次调用都重新识别）


    def __init__(self,
                 name: str,
                 mode: str,
                 method: str,
                 roi: tuple,
                 area: tuple,
                 keyword: str,
                 color_filter: Any = None) -> None:
        """

        :param name:
        :param mode:
        :param method:
        :param roi:
        :param area:
        :param keyword:
        :param color_filter: 颜色过滤配置，支持 ColorFilterConfig / dict / (method, lower, upper)
        """
        self.name = name.upper()
        if isinstance(mode, str):
            self.mode = OcrMode[mode.upper()]
        elif isinstance(mode, OcrMode):
            self.mode = mode
        if isinstance(method, str):
            self.method = OcrMethod[method.upper()]
        elif isinstance(method, OcrMethod):
            self.method = method
        self.roi: list = list(roi)
        self.area: list = list(area)
        self.keyword = keyword
        self.color_filter = ColorFilterConfig.from_value(color_filter)

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"{self.name}"

    @cached_property
    def model(self) -> Any:
        return get_ocr_model(self.lang)

    def pre_process(self, image):
        """
        重写
        :param image:
        :return:
        """
        if self.color_filter is not None:
            image = self.color_filter.apply(image)
        return image

    def prep_key(self) -> tuple:
        """
        预处理指纹：用于帧缓存判断「同样的 ROI + 同样的预处理」才能复用结果
        """
        color = None
        if self.color_filter is not None:
            color = (self.color_filter.method, tuple(map(tuple, self.color_filter.ranges)),
                     self.color_filter.binary)
        return type(self).__name__, color

    def frame_cache(self, image):
        """
        当前帧的 OCR 缓存；不可用时返回 None
        """
        if getattr(self, 'cache_disable', False):
            return None
        return FrameOcrCache.for_image(image, self.model)

    def after_process(self, result):
        """
        重写
        :param result:
        :return:
        """
        return result

    @classmethod
    def crop(cls, image: np.array, roi: tuple) -> np.array:
        """
        截取图片
        :param roi:
        :param image:
        :return:
        """
        x, y, w, h = roi
        return image[y:y + h, x:x + w]

    def ocr_item(self, image):
        """
        这个函数区别于ocr_single_line，这个函数不对图片进行裁剪，是什么就是什么的
        :param image:
        :return:
        """
        # pre process
        start_time = time.time()
        image = self.pre_process(image)
        # ocr
        result, score = self.model.ocr_single_line(image)
        if score < self.score:
            result = ""
        # after proces
        result = self.after_process(result)
        # logger.info("ocr result score: %s%s" % (result,score))
        logger.attr(name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                    text=f'[{result}]')
        return result

    def ocr_single_line(self, image):
        """
        只支持横方向的单行ocr，不支持竖方向的单行ocr
        注意：这里使用了预处理和后处理

        同一帧内相同 ROI + 相同预处理的识别结果会复用缓存
        :param image:
        :return:
        """
        # pre process
        start_time = time.time()
        roi = tuple(int(v) for v in self.roi) if self.roi else ()
        cache = self.frame_cache(image) if len(roi) == 4 else None
        key = (roi, self.prep_key()) if cache is not None else None

        if cache is not None and key in cache.single_results:
            cache.hits += 1
            result, score = cache.single_results[key]
        else:
            crop_image = self.crop(image, self.roi)
            crop_image = self.pre_process(crop_image)
            result, score = self.model.ocr_single_line(crop_image)
            if cache is not None:
                cache.misses += 1
                cache.put_single(key, result, score)

        contains_digit = any(char.isdigit() for char in result)

        if score >= self.score:
            pass
        elif score >= self.min_score and contains_digit and self.mode in [OcrMode.DIGIT, OcrMode.DIGITCOUNTER,
                                                                          OcrMode.QUANTITY]:
            logger.warning(
                f'[{self.name}] Score {score:.2f} is low, but result "{result}" contains a digit. Accepting it.')
        else:
            result = ""
        # after proces
        result = self.after_process(result)
        # logger.info("ocr result score: %s" % score)
        logger.attr(name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                    text=f'[{result}]')
        return result

    def _predict(self, image) -> list[BoxedResult]:
        """
        单条规则的常规识别：裁剪 ROI -> 预处理 -> 放大画布 -> 检测+识别
        :return: 以 ROI 左上角为原点的结果
        """
        image = self.crop(image, self.roi)
        image = self.pre_process(image)
        image = enlarge_canvas(image)
        return self.model.detect_and_ocr(image)

    def _predict_absolute(self, image, roi: tuple) -> list[BoxedResult]:
        """
        常规识别，但把结果坐标换算成全图绝对坐标（供帧缓存复用）
        """
        boxed_results = self._predict(image)
        if not roi:
            return boxed_results
        return [
            BoxedResult(box=shift_box(r.box, roi[0], roi[1]), text_img=r.text_img,
                        ocr_text=r.ocr_text, score=r.score)
            for r in boxed_results
        ]

    def _frame_raw_results(self, image, roi: tuple, cache, key) -> list:
        """
        统一的取结果入口（绝对坐标）：
            1. 规则级缓存命中
            2. 批量 mask 结果复用
            3. 常规识别并写回缓存
        """
        if cache is None:
            return self._predict_absolute(image, roi)

        cached = cache.get_rule(key)
        if cached is not None:
            cache.hits += 1
            return cached

        cached = cache.query_batch(roi)
        if cached:
            cache.hits += 1
        else:
            cached = self._predict_absolute(image, roi)
            cache.misses += 1
        cache.put_rule(key, cached)
        return cached

    def detect_and_ocr(self, image, logDisplay: bool = True) -> list[BoxedResult]:
        """
        注意：这里使用了预处理和后处理

        同一帧截图内会走 FrameOcrCache：
            - 相同 ROI + 相同预处理的结果直接复用
            - 批量 mask 识别（module.ocr.batch.prefetch）过的结果优先复用
        :param image:
        :return:
        """
        # pre process
        start_time = time.time()
        roi = tuple(int(v) for v in self.roi) if self.roi else ()
        cache = self.frame_cache(image) if len(roi) == 4 else None
        key = (roi, self.prep_key()) if cache is not None else None

        cached = self._frame_raw_results(image, roi, cache, key)
        if cache is not None:
            boxed_results = [
                BoxedResult(box=shift_box(r.box, -roi[0], -roi[1]), text_img=r.text_img,
                            ocr_text=r.ocr_text, score=r.score)
                for r in cached
            ]
        else:
            boxed_results = cached

        results = []
        # after proces
        for result in boxed_results:
            # logger.info("ocr result score: %s" % result.score)
            if result.score < self.score:
                continue
            result.ocr_text = self.after_process(result.ocr_text)
            results.append(result)
        if logDisplay:
            logger.attr(name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                        text=str([result.ocr_text for result in results]))
        return results

    def match(self, result: str, included: bool=False) -> bool:
        """
        使用ocr获取结果后和keyword进行匹配
        :param result:
        :param included:  ocr结果和keyword是否包含关系, 要么是包含关系，要么是相等关系
        :return:
        """
        if included:
            return self.keyword in result
        else:
            return self.keyword == result

    def filter(self, boxed_results: list[BoxedResult], keyword: str=None, exact: bool=False) -> list or None:
        """
        使用ocr获取结果后和keyword进行匹配. 返回匹配的index list
        :param keyword: 如果不指定默认适用对象的keyword
        :param boxed_results:
        :param exact: 是否只匹配完整的单个 OCR 文本
        :return:
        """
        # 首先先将所有的ocr的str顺序拼接起来, 然后再进行匹配
        result = None
        strings = [boxed_result.ocr_text for boxed_result in boxed_results]
        concatenated_string = "".join(strings)
        if keyword is None:
            keyword = self.keyword
        if exact:
            result = [index for index, word in enumerate(strings) if word == keyword]
            return result or None
        elif keyword in concatenated_string:
            result = [index for index, word in enumerate(strings) if keyword in word]
        else:
            result = None

        if result is not None:
            # logger.info("Filter result: %s" % result)
            return result

        # 如果适用顺序拼接还是没有匹配到，那可能是竖排的，使用单个字节的keyword进行匹配
        indices = []
        # 对于keyword中的每一个字符，都要在strings中进行匹配
        # 如果这个字符在strings中的某一个string中，那么就记录这个string的index
        max_index = len(strings) - 1
        for index, char in enumerate(keyword):
            for i, string in enumerate(strings):
                if char not in string:
                    continue
                if i <= max_index:
                    indices.append(i)
                    break
        if indices:
            # 剔除掉重复的index
            indices = list(set(indices))
            return indices
        else:
            return None

    def detect_text(self, image) -> str:
        """
        识别图片中的文字， 会按照顺序拼接起来
        :param image:
        :return:
        """
        # pre process
        start_time = time.time()
        roi = tuple(int(v) for v in self.roi) if self.roi else ()
        cache = self.frame_cache(image) if len(roi) == 4 else None
        key = (roi, self.prep_key()) if cache is not None else None
        boxed_results = self._frame_raw_results(image, roi, cache, key)
        results = ''
        # after proces
        for result in boxed_results:
            # logger.info("ocr result score: %s" % result.score)
            if result.score < self.score:
                continue
            results += result.ocr_text
        # logger.info("ocr result score: %s" % score)
        logger.attr(name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                    text=f'[{results}]')
        return results


# def test():
#     # strings = ["探", "索"]
#     # keyword = "探索"
#     # strings是截图ocr的结果，keyword是要匹配的关键字
#     strings = ['123', '456', '789', '101112', '131415', '456']
#     keyword = '123456'
#
#     indices = []
#     # 对于keyword中的每一个字符，都要在strings中进行匹配
#     # 如果这个字符在strings中的某一个string中，那么就记录这个string的index
#     max_index = len(strings) - 1
#     for index, char in enumerate(keyword):
#         for i, string in enumerate(strings):
#             if char not in string:
#                 continue
#             if i <= max_index:
#                 indices.append(i)
#                 break
#     if indices:
#         # 剔除掉重复的index
#         indices = list(set(indices))
#         return indices
#     else:
#         return None
# print(test())
