# -*- coding: utf-8 -*-
"""
批量 OCR（module/ocr/batch.py）测试：mask 合并识别 + ROI 缓存复用

    toolkit\\python.exe -m pytest tests/test_ocr_batch.py
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

from module.atom.ocr import RuleOcr  # noqa: E402
from module.ocr.batch import (  # noqa: E402
    BatchOcr,
    FrameOcrCache,
    OcrRequest,
    _area_contains,
    _box_area,
    _clip_area,
)
from module.ocr.engine import TextSystem  # noqa: E402
from module.ocr.models import profile_paths  # noqa: E402

MODEL_DIR = str(PROJECT_ROOT / 'bin' / 'ocr_model')
MODEL_AVAILABLE = profile_paths('PP-OCRv6', MODEL_DIR) is not None
requires_model = pytest.mark.skipif(not MODEL_AVAILABLE, reason='PP-OCRv6 model not found in bin/ocr_model')


@pytest.fixture(scope='module')
def engine():
    paths = profile_paths('PP-OCRv6', MODEL_DIR)
    if not paths:
        pytest.skip('PP-OCRv6 model not found in bin/ocr_model')
    return TextSystem(
        det_model_path=paths['det'],
        rec_model_path=paths['rec'],
        char_dict_path=paths['dict'],
        rec_image_shape=paths['rec_image_shape'],
        threads=4,
    )


@pytest.fixture
def scene() -> tuple:
    """
    合成一屏：三块文字，返回 (image, [(name, roi, text)])
    """
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    blocks = [
        ('left', (20, 40, 160, 60), 'STAR 100'),
        ('middle', (240, 140, 180, 60), 'QUEST 256'),
        ('right', (460, 260, 160, 60), 'MISSION'),
    ]
    for _, (x, y, w, h), text in blocks:
        cv2.putText(image, text, (x + 6, y + int(h * 0.7)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return image, blocks


# --------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------- #
def test_area_helpers():
    assert _clip_area((10, 10, 100, 100), 60, 60) == (10, 10, 50, 50)
    assert _area_contains((0, 0, 100, 100), (10, 10, 20, 20))
    assert not _area_contains((0, 0, 100, 100), (90, 90, 20, 20))

    box = np.array([[10, 20], [110, 20], [110, 60], [10, 60]], dtype=np.float32)
    assert _box_area(box) == (10, 20, 100, 40)


# --------------------------------------------------------------------- #
# mask 合并识别
# --------------------------------------------------------------------- #
def _joined(texts: list) -> str:
    """把多个文本框拼成整段（忽略空格），避免 det 分框差异影响比较"""
    return ''.join(texts).replace(' ', '')


@requires_model
def test_batch_ocr_matches_single_roi(engine, scene):
    image, blocks = scene
    requests = [OcrRequest(roi=roi, name=name) for name, roi, _ in blocks]

    batched = BatchOcr(engine).run(image, requests)
    assert batched

    for name, roi, text in blocks:
        batch_texts = [r.ocr_text for r in batched if _area_contains(roi, _box_area(r.box))]
        single = engine.detect_and_ocr(image[roi[1]:roi[1] + roi[3], roi[0]:roi[0] + roi[2]])
        single_texts = [r.ocr_text for r in single]
        # 批量识别和单独识别的内容必须一致（det 的分框方式可能略有不同）
        assert _joined(batch_texts) == _joined(single_texts), f'{name}: batch={batch_texts} single={single_texts}'
        assert text.replace(' ', '') in _joined(batch_texts)


def test_batch_ocr_skips_special_requests(engine, scene):
    image, blocks = scene
    requests = [
        OcrRequest(roi=blocks[0][1], name='a'),
        OcrRequest(roi=blocks[1][1], name='b', only_rec=True),
        OcrRequest(roi=blocks[2][1], name='c', has_color_filter=True),
    ]

    # 只剩一条有效请求，不做批量
    assert BatchOcr(engine).run(image, requests) == []


# --------------------------------------------------------------------- #
# 帧缓存
# --------------------------------------------------------------------- #
@requires_model
def test_frame_cache_singleton(engine, scene):
    image, _ = scene
    other = np.zeros_like(image)

    FrameOcrCache.clear()
    cache = FrameOcrCache.for_image(image, engine)
    assert cache is FrameOcrCache.for_image(image, engine)
    assert cache is not FrameOcrCache.for_image(other, engine)
    assert FrameOcrCache.current().image is other


@requires_model
def test_frame_cache_rule_reuse(engine, scene):
    image, blocks = scene
    name, roi, text = blocks[0]

    FrameOcrCache.clear()
    rule = RuleOcr(roi=roi, area=roi, mode='Full', method='Default', keyword='', name='cache_rule')

    first = [r.ocr_text for r in rule.detect_and_ocr(image, logDisplay=False)]
    cache = FrameOcrCache.current()
    assert cache is not None
    assert cache.misses == 1 and cache.hits == 0

    second = [r.ocr_text for r in rule.detect_and_ocr(image, logDisplay=False)]
    assert second == first
    assert cache.hits == 1

    # 新的一帧不复用
    new_image = image.copy()
    third = [r.ocr_text for r in rule.detect_and_ocr(new_image, logDisplay=False)]
    assert third == first
    assert FrameOcrCache.current() is not cache


@requires_model
def test_rule_prefetch_and_query(engine, scene):
    image, blocks = scene
    rules = [
        RuleOcr(roi=roi, area=roi, mode='Full', method='Default', keyword='', name=f'rule_{name}')
        for name, roi, _ in blocks
    ]

    # 单条规则结果（不使用缓存）
    expected = {}
    for rule in rules:
        FrameOcrCache.clear()
        expected[rule.name] = [r.ocr_text for r in rule.detect_and_ocr(image, logDisplay=False)]

    # 批量识别 + 缓存复用
    FrameOcrCache.clear()
    count = RuleOcr.prefetch(image, rules)
    assert count > 0

    actual = {}
    for rule in rules:
        actual[rule.name] = [r.ocr_text for r in rule.detect_and_ocr(image, logDisplay=False)]

    cache = FrameOcrCache.current()
    assert actual == expected
    assert cache.hits >= 1
    assert cache.batch_ready


def test_rule_ocr_request_modes():
    full = RuleOcr(roi=(0, 0, 100, 40), area=(0, 0, 100, 40), mode='Full', method='Default', keyword='', name='full')
    digit = RuleOcr(roi=(0, 0, 100, 40), area=(0, 0, 100, 40), mode='Digit', method='Default', keyword='0', name='digit')

    assert full.ocr_request().only_rec is False
    assert digit.ocr_request().only_rec is True
    assert full.ocr_request().roi == (0, 0, 100, 40)
