# This Python file uses the following encoding: utf-8
"""
基于 RapidOCR 的本地 OCR 引擎封装

设计要点：
    1. 模型可替换：det / rec / 字典 都走本地路径，默认 PP-OCRv6 small
    2. onnxruntime CPU 线程数可配置（默认 4）
    3. 结果结构沿用旧实现的 BoxedResult（box/ocr_text/score），业务层无感
    4. 支持竖排文本的整块识别（det-only 后再对每个文本框做识别）
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from module.logger import logger

# 与 MaaFramework 的 CPU 线程配置保持一致
DEFAULT_CPU_THREADS = 4
DEFAULT_TEXT_SCORE = 0.5

# 竖排文本框判定：高宽比超过该值时旋转为横向再识别
VERTICAL_RATIO = 1.5


@dataclass
class BoxedResult:
    """
    单个文本框的识别结果

    :param box: (4, 2) 的四点坐标，顺序为 左上/右上/右下/左下
    :param text_img: 文本框裁剪图，可为 None
    :param ocr_text: 识别文本
    :param score: 置信度
    """
    box: np.ndarray
    text_img: Optional[np.ndarray]
    ocr_text: str
    score: float

    def area(self) -> Tuple[int, int, int, int]:
        """返回 (x, y, w, h)"""
        return (
            int(self.box[0, 0]),
            int(self.box[0, 1]),
            int(self.box[1, 0] - self.box[0, 0]),
            int(self.box[2, 1] - self.box[0, 1]),
        )

    def __str__(self) -> str:
        return 'BoxedResult[%s, %s]' % (self.ocr_text, self.score)

    def __repr__(self) -> str:
        return self.__str__()


def rotate_vertical(image: np.ndarray) -> np.ndarray:
    """高瘦图片旋转为横向，用于竖排文本"""
    height, width = image.shape[0:2]
    if height * 1.0 / width >= VERTICAL_RATIO:
        return np.rot90(image)
    return image


def crop_box(image: np.ndarray, box: np.ndarray) -> np.ndarray:
    """
    按四点坐标做透视裁剪（含竖排旋转），与 PaddleOCR 的 get_rotate_crop_image 一致
    """
    points = np.asarray(box, dtype=np.float32)
    crop_width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    crop_height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    crop_width = max(crop_width, 1)
    crop_height = max(crop_height, 1)
    pts_std = np.float32([[0, 0], [crop_width, 0], [crop_width, crop_height], [0, crop_height]])
    matrix = cv2.getPerspectiveTransform(points, pts_std)
    dst_img = cv2.warpPerspective(
        image, matrix, (crop_width, crop_height),
        borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC,
    )
    return rotate_vertical(dst_img)


class TextSystem:
    """
    RapidOCR 引擎封装，接口与旧版 ppocr-onnx 的 TextSystem 对齐：
        detect_and_ocr / ocr_single_line / ocr_lines / warmup
    """

    def __init__(
            self,
            use_angle_cls: bool = False,
            rec_model_path: Optional[str] = None,
            det_model_path: Optional[str] = None,
            char_dict_path: Optional[str] = None,
            rec_image_shape: Sequence[int] = None,
            threads: int = DEFAULT_CPU_THREADS,
            text_score: float = DEFAULT_TEXT_SCORE,
            rec_batch_num: int = 6,
            engine_type: str = "onnxruntime",
            log_level: str = "error",
            **extra_params: Any,
    ) -> None:
        from rapidocr import RapidOCR

        self.threads = int(threads)
        self.text_score = float(text_score)
        self.rec_image_shape = list(rec_image_shape or (3, 48, 320))

        params: Dict[str, Any] = {
            'Global.text_score': self.text_score,
            'Global.use_cls': bool(use_angle_cls),
            'Global.log_level': str(log_level),
            'EngineConfig.onnxruntime.intra_op_num_threads': self.threads,
            'EngineConfig.onnxruntime.inter_op_num_threads': self.threads,
            'EngineConfig.onnxruntime.enable_cpu_mem_arena': False,
            'Rec.rec_batch_num': int(rec_batch_num),
            'Rec.rec_img_shape': list(self.rec_image_shape),
        }
        if det_model_path:
            params['Det.model_path'] = str(det_model_path)
        if rec_model_path:
            params['Rec.model_path'] = str(rec_model_path)
        if char_dict_path:
            params['Rec.rec_keys_path'] = str(char_dict_path)
        for key, value in extra_params.items():
            if value is not None:
                params[key] = value

        self.use_angle_cls = bool(use_angle_cls)
        self.det_model_path = str(det_model_path) if det_model_path else None
        self.rec_model_path = str(rec_model_path) if rec_model_path else None
        self.char_dict_path = str(char_dict_path) if char_dict_path else None

        start_time = time.time()
        self._engine = RapidOCR(params=params)
        self._loaded = False
        # rapidocr 的 update_params 会修改实例状态，这里记下检测参数的默认值，
        # 每次调用都显式传入，避免上一次调用（比如只有识别）影响下一次
        self._default_box_thresh = float(self._engine.cfg.Det.box_thresh)
        self._default_unclip_ratio = float(self._engine.cfg.Det.unclip_ratio)
        logger.attr(name='OCR init', text=f'{time.time() - start_time:.2f}s')

    def __str__(self) -> str:
        return 'RapidOCR TextSystem'

    # ------------------------------------------------------------------ #
    # 模型加载
    # ------------------------------------------------------------------ #
    def warmup(self) -> bool:
        """
        提前加载模型（第一次推理会比较慢），加载失败返回 False
        """
        if self._loaded:
            return True
        try:
            dummy = np.zeros((32, 320, 3), dtype=np.uint8)
            self._engine(dummy, use_det=True, use_cls=False, use_rec=True, text_score=0.0)
            self._loaded = True
            return True
        except Exception as e:
            logger.error(f'OCR model load failed: {e}')
            return False
    # ------------------------------------------------------------------ #
    # 识别接口
    # ------------------------------------------------------------------ #
    def detect_and_ocr(
            self,
            image: np.ndarray,
            drop_score: float = None,
            unclip_ratio: float = None,
            box_thresh: float = None,
            vertical: bool = False,
    ) -> List[BoxedResult]:
        """
        检测 + 识别整张图片，返回所有文本框

        :param drop_score: 置信度过滤阈值，默认使用构造参数 text_score
        :param vertical: 是否按竖排文本识别（det 出框后旋转再识别）
        """
        if image is None or image.size == 0:
            return []

        if vertical:
            return self._detect_and_ocr_vertical(image, drop_score=drop_score,
                                                 unclip_ratio=unclip_ratio, box_thresh=box_thresh)

        try:
            output = self._engine(
                image,
                use_det=True,
                use_cls=False,
                use_rec=True,
                text_score=self.text_score if drop_score is None else float(drop_score),
                unclip_ratio=unclip_ratio if unclip_ratio is not None else self._default_unclip_ratio,
                box_thresh=box_thresh if box_thresh is not None else self._default_box_thresh,
            )
        except Exception as e:
            logger.error(f'OCR detect_and_ocr failed: {e}')
            return []

        boxes, txts, scores = output.boxes, output.txts, output.scores
        if boxes is None or txts is None or scores is None:
            return []

        results: List[BoxedResult] = []
        for box, text, score in zip(boxes, txts, scores):
            results.append(BoxedResult(
                box=np.asarray(box, dtype=np.float32),
                text_img=None,
                ocr_text=str(text),
                score=float(score),
            ))
        return results

    def _detect_and_ocr_vertical(
            self,
            image: np.ndarray,
            drop_score: float = None,
            unclip_ratio: float = None,
            box_thresh: float = None,
    ) -> List[BoxedResult]:
        """
        竖排文本：先只做检测拿到文本框，再对每个框旋转后单行识别
        """
        try:
            det_output = self._engine(image, use_det=True, use_cls=False, use_rec=False,
                                      text_score=self.text_score,
                                      unclip_ratio=unclip_ratio if unclip_ratio is not None else self._default_unclip_ratio,
                                      box_thresh=box_thresh if box_thresh is not None else self._default_box_thresh)
        except Exception as e:
            logger.error(f'OCR vertical detect failed: {e}')
            return []

        boxes = det_output.boxes
        if boxes is None:
            return []

        results: List[BoxedResult] = []
        for box in boxes:
            box = np.asarray(box, dtype=np.float32)
            crop = crop_box(image, box)
            text, score = self.ocr_single_line(crop, rotate=False)
            if not text:
                continue
            if drop_score is not None and score < drop_score:
                continue
            results.append(BoxedResult(box=box, text_img=crop, ocr_text=text, score=score))
        return results

    def ocr_single_line(self, image: np.ndarray, rotate: bool = True) -> Tuple[str, float]:
        """
        只识别（不做检测），用于固定 ROI 的单行文本

        :return: (text, score)，识别失败返回 ("", 0.0)
        """
        if image is None or image.size == 0:
            return '', 0.0
        if rotate:
            image = rotate_vertical(image)
        try:
            output = self._engine(image, use_det=False, use_cls=False, use_rec=True,
                                  text_score=self.text_score)
        except Exception as e:
            logger.error(f'OCR ocr_single_line failed: {e}')
            return '', 0.0

        txts, scores = output.txts, output.scores
        if not txts:
            return '', 0.0
        return str(txts[0]), float(scores[0])

    def ocr_lines(self, images: Sequence[np.ndarray]) -> List[Tuple[str, float]]:
        """批量单行识别（RapidOCR 未提供纯识别批量接口，这里逐张处理）"""
        return [self.ocr_single_line(image) for image in images]

    def ocr_rec_images(self, images: Sequence[np.ndarray]) -> List[Tuple[str, float]]:
        """对已经裁剪好的文本框做识别（不做竖排旋转，筐里已经是正的）"""
        return [self.ocr_single_line(image, rotate=False) for image in images]
