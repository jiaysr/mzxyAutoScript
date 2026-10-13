# -*- coding: utf-8 -*-
"""
颜色过滤（color_filter）测试

    toolkit\\python.exe -m pytest tests/test_ocr_color_filter.py
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
from module.ocr.engine import TextSystem  # noqa: E402
from module.ocr.models import profile_paths  # noqa: E402
from module.ocr.utils import ColorFilterConfig  # noqa: E402

MODEL_DIR = str(PROJECT_ROOT / 'bin' / 'ocr_model')


def green_white_scene() -> np.ndarray:
    """绿底白字的合成图（RGB）"""
    image = np.zeros((120, 360, 3), dtype=np.uint8)
    image[:] = (0, 180, 0)
    cv2.putText(image, '12345', (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 4)
    return image


def test_from_value_variants():
    assert ColorFilterConfig.from_value(None) is None

    config = ColorFilterConfig.from_value({'method': 'HSV', 'lower': [0, 0, 200], 'upper': [180, 60, 255]})
    assert config.method == 'HSV'
    assert config.ranges == [([0, 0, 200], [180, 60, 255])]

    config = ColorFilterConfig.from_value(('HSV', (0, 0, 200), (180, 60, 255)))
    assert config.ranges == [((0, 0, 200), (180, 60, 255))]

    exist = ColorFilterConfig(method='GRAY', ranges=[((200,), (255,))])
    assert ColorFilterConfig.from_value(exist) is exist

    with pytest.raises(ValueError):
        ColorFilterConfig.from_value(123)


def test_apply_binary_keeps_selected_color():
    image = green_white_scene()
    config = ColorFilterConfig(method='HSV', ranges=[((0, 0, 200), (180, 60, 255))])

    filtered = config.apply(image)

    assert filtered.shape == image.shape
    # 白字区域保留白色像素，绿底被过滤成黑色
    text_region = filtered[20:90, 20:200]
    assert np.any(np.all(text_region == 255, axis=-1))
    assert np.all(filtered[105:115, 300:355] == 0)


def test_apply_keeps_original_when_not_binary():
    image = green_white_scene()
    config = ColorFilterConfig(method='HSV', ranges=[((0, 0, 200), (180, 60, 255))], binary=False)

    filtered = config.apply(image)

    # 背景涂黑
    assert np.all(filtered[105:115, 300:355] == 0)
    # 文字保留原色
    text_mask = np.all(image[20:90, 20:200] == 255, axis=-1)
    assert np.any(text_mask)
    assert np.array_equal(filtered[20:90, 20:200][text_mask], image[20:90, 20:200][text_mask])


def test_rule_pre_process_applies_color_filter():
    image = green_white_scene()
    rule = RuleOcr(roi=(0, 0, 360, 120), area=(0, 0, 360, 120), mode='Full', method='Default', keyword='',
                   name='filter_rule', color_filter={'method': 'HSV', 'lower': [0, 0, 200], 'upper': [180, 60, 255]})

    assert rule.color_filter is not None
    filtered = rule.pre_process(image)
    text_region = filtered[20:90, 20:200]
    assert np.any(np.all(text_region == 255, axis=-1))
    assert np.all(filtered[105:115, 300:355] == 0)

    plain = RuleOcr(roi=(0, 0, 360, 120), area=(0, 0, 360, 120), mode='Full', method='Default', keyword='',
                    name='plain_rule')
    assert plain.color_filter is None
    assert np.array_equal(plain.pre_process(image), image)


def test_rule_ocr_request_marks_color_filter():
    rule = RuleOcr(roi=(0, 0, 360, 120), area=(0, 0, 360, 120), mode='Full', method='Default', keyword='',
                   name='filter_rule', color_filter={'method': 'HSV', 'lower': [0, 0, 200], 'upper': [180, 60, 255]})
    assert rule.ocr_request().has_color_filter is True


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


def test_color_filter_image_is_recognizable(engine):
    """过滤后的图仍然能正常识别出文字"""
    image = green_white_scene()
    rule = RuleOcr(roi=(0, 0, 360, 120), area=(0, 0, 360, 120), mode='Full', method='Default', keyword='',
                   name='filter_rule', color_filter={'method': 'HSV', 'lower': [0, 0, 200], 'upper': [180, 60, 255]})

    text, score = engine.ocr_single_line(rule.pre_process(image))

    assert text.replace(' ', '') == '12345'
    assert score > 0.6
