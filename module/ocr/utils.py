# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np


def _merge_area(area1, area2):
    xa1, ya1, xa2, ya2 = area1
    xb1, yb1, xb2, yb2 = area2
    return min(xa1, xb1), min(ya1, yb1), max(xa2, xb2), max(ya2, yb2)

def merge_area(areas: list[tuple]) -> tuple:
    """
    合并多个区域
    :param areas:
    :return:
    """
    if not areas:
        return 0, 0, 0, 0
    area = areas[0]
    for i in range(1, len(areas)):
        area = _merge_area(area, areas[i])
    return area


# --------------------------------------------------------------------- #
# 颜色过滤（color_filter）
# --------------------------------------------------------------------- #
# 参考 MaaFramework 的 color_filter：先用颜色把 ROI 二值化，再送 OCR。
# 适合「彩色底 + 彩色字」「描边字」之类直接 OCR 识别不稳的场景。
#
# 注意：本项目的截图是 RGB 的（module/device 里统一转成了 RGB），
# 这里的方法名都按 RGB 输入来定义，颜色范围直接从截图/取色工具里取即可。
COLOR_METHODS: Dict[str, Optional[int]] = {
    'RGB': None,  # 不转换
    'BGR': cv2.COLOR_RGB2BGR,
    'HSV': cv2.COLOR_RGB2HSV,
    'GRAY': cv2.COLOR_RGB2GRAY,
    'LAB': cv2.COLOR_RGB2LAB,
    'HLS': cv2.COLOR_RGB2HLS,
}


@dataclass
class ColorFilterConfig:
    """
    颜色过滤配置

    :param method: 颜色空间，可选 BGR / RGB / HSV / GRAY / LAB / HLS
    :param ranges: 保留的颜色范围，形如 [((lower...), (upper...)), ...]
    :param binary: True 时输出白字黑底二值图（与 MaaFramework 一致），
                   False 时只把范围外的像素涂黑、保留文字原色
    """
    method: str = 'HSV'
    ranges: Sequence[Tuple[Sequence[int], Sequence[int]]] = field(default_factory=list)
    binary: bool = True

    @classmethod
    def from_value(cls, value: Any) -> Optional["ColorFilterConfig"]:
        """
        支持 dict / tuple / ColorFilterConfig 三种写法：
            ColorFilterConfig(method="HSV", ranges=[((0, 80, 80), (10, 255, 255))])
            {'method': 'HSV', 'lower': [0, 80, 80], 'upper': [10, 255, 255]}
            ('HSV', (0, 80, 80), (10, 255, 255))
        """
        if value is None:
            return None
        if isinstance(value, ColorFilterConfig):
            return value
        if isinstance(value, dict):
            config = dict(value)
            if 'ranges' not in config and 'lower' in config and 'upper' in config:
                config['ranges'] = [(config.pop('lower'), config.pop('upper'))]
            return cls(**config)
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            method, lower, upper = value[0], value[1], value[2]
            return cls(method=method, ranges=[(lower, upper)])
        raise ValueError(f'Unsupported color_filter: {value!r}')

    def apply(self, image: np.ndarray) -> np.ndarray:
        """
        颜色过滤后返回与输入同尺寸的 BGR 图

        :param image: BGR 图片
        :return: 过滤后的 BGR 图片
        """
        if not self.ranges:
            return image

        method = COLOR_METHODS.get(str(self.method).upper())
        if str(self.method).upper() not in COLOR_METHODS:
            raise ValueError(f'Unsupported color method: {self.method}')

        color = image if method is None else cv2.cvtColor(image, method)

        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        for lower, upper in self.ranges:
            lower = np.array(lower, dtype=np.uint8)
            upper = np.array(upper, dtype=np.uint8)
            mask = cv2.bitwise_or(mask, cv2.inRange(color, lower, upper))

        if self.binary:
            # 与 MaaFramework 一致：白字黑底
            return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

        return cv2.bitwise_and(image, image, mask=mask)
