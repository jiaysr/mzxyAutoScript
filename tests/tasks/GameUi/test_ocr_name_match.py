# -*- coding: utf-8 -*-
"""OCR 名称匹配回归测试（不连设备）

2026-11-15 23:20 真机（仙居福地 淼的房间）：目标名被读成「水夹」，
报错前 `ocr_name_match` 走「name in ocr_text」分支直接返回 True ——
「淼」的别名「水」包含在「水夹」里，于是把一只无法攻击的其它怪当成 Boss，
技能一直放、人卡住不动。

修法：**单字名称要求整体相等**（仙居福地的 焱/垚/森/淼/鑫），
多字名称仍然允许包含，方便容忍「沙灵神像」这类多的字。
"""
from tasks.GameUi.core.ocr import OcrHelpers


def test_single_char_name_requires_exact_match():
    assert OcrHelpers.ocr_name_match('淼', '淼') is True
    assert OcrHelpers.ocr_name_match('水', '水') is True          # OCR 把淼读成水
    # 带该字的其它怪不是同一个目标
    assert OcrHelpers.ocr_name_match('水夹', '水') is False
    assert OcrHelpers.ocr_name_match('水灵', '水') is False
    assert OcrHelpers.ocr_name_match('仙居门神', '门') is False


def test_single_char_name_ignores_spaces():
    assert OcrHelpers.ocr_name_match(' 淼 ', '淼') is True
    # 单字名后面还挂着别的汉字（淼精英）就不是同一个名字了：保持严格
    assert OcrHelpers.ocr_name_match('淼精英', '淼') is False


def test_multi_char_name_still_allows_containment():
    assert OcrHelpers.ocr_name_match('沙灵神', '沙灵神') is True
    assert OcrHelpers.ocr_name_match('沙灵神(精英)', '沙灵神') is True
    # 前两字不同 -> 不匹配
    assert OcrHelpers.ocr_name_match('舞娘', '沙灵神') is False


def test_multi_char_name_fuzzy_match():
    # 前两字一致 + 相似度达标（>=0.7）：容忍末尾形近字误识（4 字错 1 字 = 0.75）
    assert OcrHelpers.ocr_name_match('蚩尤使考', '蚩尤使者') is True
    # 3 字错 1 字只有 0.667，达不到门槛 -> 这种要登记别名（如 梦魇兽 -> 梦魔兽）
    assert OcrHelpers.ocr_name_match('梦魔兽', '梦魇兽') is False
    assert OcrHelpers.ocr_name_match('梦魔兽', '梦魔兽') is True
    # 2 字名不走模糊分支（前两字必须一致那条对它没意义）
    assert OcrHelpers.ocr_name_match('沙主', '沙王') is False
