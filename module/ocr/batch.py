# This Python file uses the following encoding: utf-8
"""
批量 OCR：MaaFramework 风格的 mask 合并识别 + ROI 缓存复用

背景：
    项目里一屏截图经常会连续查多个 OCR 规则（多个关键词、多个列表区域），
    每个规则单独跑一遍 det+rec 非常浪费。

本模块提供两层优化：
    1. BatchOcr —— 把多个规则的 ROI 贴到同一张全黑画布上（mask），只跑一次 det+rec，
       再按「文本框与 ROI 的相交关系」把结果分回各个规则（MaaFramework 的做法）
    2. FrameOcrCache —— 同一帧截图内的结果缓存：
       - 完全包含：ROI 内已有识别结果，直接复用，不再推理
       - 部分相交：只对相交区域做一次 rec（不重新 det）
       - 未命中：正常跑一遍，并把结果写回缓存
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from module.logger import logger
from module.ocr.engine import BoxedResult, TextSystem

# det 输入的最大边长，超过该值 det 会整体降采样导致小字漏检（与 MaaFramework 一致）
DEFAULT_MAX_SIDE_LEN = 960
# 合并识别时的置信度阈值，故意放低，交给各规则自己按 score/min_score 过滤
DEFAULT_PREFETCH_TEXT_SCORE = 0.3
# 单帧最多缓存多少条规则结果，防止长时间复用同一帧时无限增长
MAX_FRAME_CACHE_ENTRIES = 128

Area = Tuple[int, int, int, int]


def _clip_area(area: Area, width: int, height: int) -> Area:
    x, y, w, h = area
    x = max(0, min(int(x), width - 1))
    y = max(0, min(int(y), height - 1))
    w = max(0, min(int(w), width - x))
    h = max(0, min(int(h), height - y))
    return x, y, w, h


def _area_contains(outer: Area, inner: Area) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


def _box_area(box: np.ndarray) -> Area:
    x = int(np.min(box[:, 0]))
    y = int(np.min(box[:, 1]))
    w = int(np.max(box[:, 0])) - x
    h = int(np.max(box[:, 1])) - y
    return x, y, w, h


def _intersect(box: np.ndarray, roi: Area) -> Optional[Area]:
    bx, by, bw, bh = _box_area(box)
    x1 = max(bx, roi[0])
    y1 = max(by, roi[1])
    x2 = min(bx + bw, roi[0] + roi[2])
    y2 = min(by + bh, roi[1] + roi[3])
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2 - x1, y2 - y1


def shift_box(box: np.ndarray, dx: int, dy: int) -> np.ndarray:
    shifted = np.array(box, dtype=np.float32, copy=True)
    shifted[:, 0] += dx
    shifted[:, 1] += dy
    return shifted


@dataclass
class OcrRequest:
    """
    一条待识别的 OCR 请求（一般来自 RuleOcr 的 roi）

    :param roi: 绝对坐标 (x, y, w, h)
    :param name: 规则名，仅用于日志
    :param only_rec: 只识别不检测，不参与 batch（与 MaaFramework 一致）
    :param has_color_filter: 带颜色过滤的规则不参与 batch（mask 图无法共享）
    """
    roi: Area
    name: str = ''
    only_rec: bool = False
    has_color_filter: bool = False


class BatchOcr:
    """
    把多个 ROI 合并成一次 det+rec

    参考 MaaFramework 的 mask 实现：
        - 不改变各 ROI 的像素，直接把 ROI 贴到全黑画布上
        - 按 max_side_len 分簇，避免合并后超过 det 的降采样拐点、导致小字漏检
    """

    def __init__(self, model: TextSystem, max_side_len: int = DEFAULT_MAX_SIDE_LEN,
                 text_score: float = DEFAULT_PREFETCH_TEXT_SCORE) -> None:
        self.model = model
        self.max_side_len = int(max_side_len or DEFAULT_MAX_SIDE_LEN)
        self.text_score = float(text_score)

    def run(self, image: np.ndarray, requests: Sequence[OcrRequest]) -> List[BoxedResult]:
        """
        :return: 所有簇的识别结果（绝对坐标），未按规则划分，由缓存层再分配
        """
        valid = [r for r in requests if not r.only_rec and not r.has_color_filter and r.roi[2] > 0 and r.roi[3] > 0]
        if len(valid) < 2:
            return []

        height, width = image.shape[:2]
        clusters = self._cluster(valid, width, height)
        if not clusters:
            return []

        results: List[BoxedResult] = []
        for cluster in clusters:
            masked = np.zeros(image.shape, dtype=image.dtype)
            for request in cluster:
                x, y, w, h = _clip_area(request.roi, width, height)
                if w <= 0 or h <= 0:
                    continue
                masked[y:y + h, x:x + w] = image[y:y + h, x:x + w]

            start_time = time.time()
            cluster_results = self.model.detect_and_ocr(masked, drop_score=self.text_score)
            results.extend(cluster_results)
            logger.attr(
                name='BatchOcr %ss' % round(time.time() - start_time, 3),
                text=f'{len(cluster)} rois -> {len(cluster_results)} boxes',
            )

        return results

    def _cluster(self, requests: Sequence[OcrRequest], width: int, height: int) -> List[List[OcrRequest]]:
        """
        贪心分簇：优先并入不超过 max_side_len 的簇，装不下就开新簇
        """
        clusters: List[List[OcrRequest]] = []
        unions: List[Area] = []

        for request in requests:
            roi = _clip_area(request.roi, width, height)
            if roi[2] <= 0 or roi[3] <= 0:
                continue

            for index, union in enumerate(unions):
                merged_w = max(union[0] + union[2], roi[0] + roi[2]) - min(union[0], roi[0])
                merged_h = max(union[1] + union[3], roi[1] + roi[3]) - min(union[1], roi[1])
                if max(merged_w, merged_h) <= self.max_side_len:
                    clusters[index].append(request)
                    x = min(union[0], roi[0])
                    y = min(union[1], roi[1])
                    unions[index] = (x, y, merged_w, merged_h)
                    break
            else:
                clusters.append([request])
                unions.append(roi)

        return clusters


class FrameOcrCache:
    """
    同一帧截图内的 OCR 结果缓存（按 image 对象身份判定是否为同一帧）
    """

    _single: Optional["FrameOcrCache"] = None

    def __init__(self, image: np.ndarray, model: TextSystem) -> None:
        self.image = image  # 强引用，保证 id() 不会被复用
        self.model = model
        self.rule_results: Dict[tuple, List[BoxedResult]] = {}
        self.single_results: Dict[tuple, tuple] = {}
        self.batch_results: List[BoxedResult] = []
        self.batch_ready = False
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------ #
    # 单例管理
    # ------------------------------------------------------------------ #
    @classmethod
    def for_image(cls, image: np.ndarray, model: TextSystem) -> Optional["FrameOcrCache"]:
        """
        获取当前帧的缓存；image 或 model 变了就重置
        """
        if image is None or model is None:
            return None
        current = cls._single
        if current is not None and current.image is image and current.model is model:
            return current
        cls._single = cls(image, model)
        return cls._single

    @classmethod
    def current(cls) -> Optional["FrameOcrCache"]:
        return cls._single

    @classmethod
    def clear(cls) -> None:
        cls._single = None

    # ------------------------------------------------------------------ #
    # 规则级缓存
    # ------------------------------------------------------------------ #
    def get_rule(self, key: tuple) -> Optional[List[BoxedResult]]:
        return self.rule_results.get(key)

    def put_rule(self, key: tuple, results: List[BoxedResult]) -> None:
        self.rule_results[key] = results
        if len(self.rule_results) > MAX_FRAME_CACHE_ENTRIES:
            # 超限就整体清空，简单粗暴但足够安全
            logger.warning(f'FrameOcrCache rule entries exceed {MAX_FRAME_CACHE_ENTRIES}, reset')
            self.rule_results.clear()
            self.single_results.clear()
            self.batch_results.clear()
            self.batch_ready = False

    def put_single(self, key: tuple, text: str, score: float) -> None:
        """缓存单行识别结果"""
        self.single_results[key] = (text, float(score))

    # ------------------------------------------------------------------ #
    # 批量识别（mask 合并）
    # ------------------------------------------------------------------ #
    def prefetch(self, requests: Sequence[OcrRequest], max_side_len: int = DEFAULT_MAX_SIDE_LEN,
                 text_score: float = DEFAULT_PREFETCH_TEXT_SCORE) -> int:
        """
        预取：把多个 ROI 合并成一次识别，结果放进 batch_cache
        :return: 识别到的文本框数量
        """
        if self.batch_ready:
            return len(self.batch_results)
        self.batch_results = BatchOcr(self.model, max_side_len, text_score).run(self.image, requests)
        self.batch_ready = True
        if self.batch_results:
            logger.info(f'FrameOcrCache prefetch {len(requests)} rois -> {len(self.batch_results)} boxes')
        return len(self.batch_results)

    # ------------------------------------------------------------------ #
    # 复用查询（Maa 的 contains / intersect 逻辑）
    # ------------------------------------------------------------------ #
    def query_batch(self, roi: Area) -> List[BoxedResult]:
        """
        从批量结果里取该 ROI 的结果：
            - 完全包含：直接复用
            - 部分相交：对相交区域做一次 rec
        """
        if not self.batch_results:
            return []

        results: List[BoxedResult] = []
        for boxed in self.batch_results:
            box_area = _box_area(boxed.box)
            if _area_contains(roi, box_area):
                results.append(boxed)
                continue

            intersection = _intersect(boxed.box, roi)
            if intersection is None:
                continue

            x, y, w, h = intersection
            text, score = self.get_rec(self.image[y:y + h, x:x + w])
            if not text:
                continue
            results.append(BoxedResult(
                box=np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32),
                text_img=None,
                ocr_text=text,
                score=score,
            ))
        return results

    def get_rec(self, image_crop: np.ndarray) -> Tuple[str, float]:
        """只做识别（不检测）"""
        return self.model.ocr_single_line(image_crop, rotate=False)

    def stats(self) -> str:
        return f'hit={self.hits} miss={self.misses} batch={len(self.batch_results)}'


def prefetch(image: np.ndarray, requests: Sequence[OcrRequest], model: TextSystem,
             max_side_len: int = DEFAULT_MAX_SIDE_LEN,
             text_score: float = DEFAULT_PREFETCH_TEXT_SCORE) -> int:
    """
    便捷入口：对当前帧的若干 ROI 做合并识别

    :param image: 当前截图
    :param requests: OcrRequest 列表
    :param model: OCR 引擎
    :return: 识别到的文本框数量
    """
    cache = FrameOcrCache.for_image(image, model)
    if cache is None or not requests:
        return 0
    return cache.prefetch(requests, max_side_len=max_side_len, text_score=text_score)
