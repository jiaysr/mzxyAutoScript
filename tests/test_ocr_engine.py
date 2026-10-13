# -*- coding: utf-8 -*-
"""
RapidOCR 引擎（module/ocr/engine.py）的功能测试

用合成图验证 det/rec 流程，不依赖游戏截图；模型不存在时自动跳过。
    toolkit\\python.exe -m pytest tests/test_ocr_engine.py
"""
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.ocr.engine import TextSystem, crop_box, rotate_vertical  # noqa: E402
from module.ocr.models import profile_paths  # noqa: E402

MODEL_DIR = str(PROJECT_ROOT / 'bin' / 'ocr_model')
TEST_VERSION = 'PP-OCRv6'


def text_image(lines: list, size=(200, 500)) -> np.ndarray:
    """画一张黑底白字的合成图"""
    image = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    for index, text in enumerate(lines):
        cv2.putText(image, text, (20, 60 + index * 70), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3)
    return image


@pytest.fixture(scope='module')
def engine():
    paths = profile_paths(TEST_VERSION, MODEL_DIR)
    if not paths:
        pytest.skip(f'OCR model {TEST_VERSION} not found in {MODEL_DIR}')
    return TextSystem(
        det_model_path=paths['det'],
        rec_model_path=paths['rec'],
        char_dict_path=paths['dict'],
        rec_image_shape=paths['rec_image_shape'],
        threads=4,
    )


def test_ocr_single_line_digits(engine):
    image = text_image(['12345'])
    text, score = engine.ocr_single_line(image)

    assert text.replace(' ', '') == '12345'
    assert score > 0.6


def test_detect_and_ocr_two_lines(engine):
    image = text_image(['ABC 888', 'XYZ 999'])
    results = engine.detect_and_ocr(image)
    texts = [result.ocr_text for result in results]

    assert 'ABC' in texts and '888' in texts
    assert 'XYZ' in texts and '999' in texts
    for result in results:
        assert result.box.shape == (4, 2)
        assert result.score > 0.5


def test_detect_after_rec_only(engine):
    """
    回归测试：rapidocr 的 update_params 会修改实例状态，
    只做识别的调用不能影响后续的检测
    """
    image = text_image(['ABC 888', 'XYZ 999'])
    engine.ocr_single_line(image)
    results = engine.detect_and_ocr(image)

    assert len(results) >= 2


def test_cpu_thread_num_applied(engine):
    onnx_cfg = engine._engine.cfg.EngineConfig.onnxruntime
    assert onnx_cfg.intra_op_num_threads == 4
    assert onnx_cfg.inter_op_num_threads == 4


def test_rotate_vertical():
    tall = np.zeros((100, 40, 3), dtype=np.uint8)
    wide = np.zeros((40, 100, 3), dtype=np.uint8)

    assert rotate_vertical(tall).shape[:2] == (40, 100)
    assert rotate_vertical(wide).shape[:2] == (40, 100)


def test_crop_box():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[20:60, 30:130] = 255
    box = np.array([[30, 20], [130, 20], [130, 60], [30, 60]], dtype=np.float32)

    crop = crop_box(image, box)

    assert crop.shape[0] == 40
    assert crop.shape[1] == 100
