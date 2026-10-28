# -*- coding: utf-8 -*-
"""
战斗技能目录 / 识别 / 排序计划 单测（不连设备）

覆盖：
- 技能目录：结构合法、id 唯一、图标能读、默认顺序完整
- 顺序归一化：去重、未知 id、新增技能补齐、禁用剔除
- 技能位识别：把图标贴回 1280x720 截图，验证「识别出的技能位 -> 技能」正确，
  以及图标互不混淆（每个图标的最优匹配都是它自己）
- 抗干扰：亮度/伽马/模糊后仍然认得出自己
- 释放计划：按预设顺序排、禁用的不点、未识别的排最后
- 配置模型：预设默认值、老配置兼容

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_skills.py
装了 pytest 时也可以：python -m pytest tests/test_battle_skills.py
"""
import sys
import traceback
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.catalog import load_catalog
from tasks.Battle.planner import build_plan, plan_slots, plan_summary
from tasks.Battle.recognizer import (DEFAULT_THRESHOLD, SkillRecognizer, SlotMatch,
                                     load_icon, similarity)
from tasks.Battle.slots import BATTLE_SKILL_CHECK_REGIONS, SKILL_ICON_INSET

SCREEN_SIZE = (720, 1280)


def catalog_ids():
    return [skill.id for skill in load_catalog().skills]


def slot_icon_region(slot: int):
    """某个技能位里「图标本体」的绝对区域"""
    x, y, _, _ = BATTLE_SKILL_CHECK_REGIONS[slot]
    ix, iy, iw, ih = SKILL_ICON_INSET
    return x + ix, y + iy, iw, ih


def build_scene(placements: dict) -> np.ndarray:
    """
    造一张假截图：黑底 + 把技能图标贴到对应技能位的「图标本体」位置

    :param placements: {技能位下标: 技能 id}
    """
    import cv2

    catalog = load_catalog()
    scene = np.zeros((*SCREEN_SIZE, 3), dtype=np.uint8)
    for slot, skill_id in placements.items():
        icon, mask = load_icon(catalog.icon_path(skill_id))
        x, y, w, h = slot_icon_region(slot)
        icon = cv2.resize(icon, (w, h), interpolation=cv2.INTER_AREA).astype(np.uint8)
        mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        region = scene[y:y + h, x:x + w]
        region[mask] = icon[mask]
        scene[y:y + h, x:x + w] = region
    return scene


# --------------------------------------------------------------------- 目录
def test_catalog_shape():
    """目录结构合法：有职业、有技能、id 唯一、每个技能都挂在存在的职业上"""
    catalog = load_catalog()
    assert len(catalog.classes) >= 1, '至少要有一个职业'
    assert len(catalog.skills) >= 1, '至少要有一个技能'
    ids = [skill.id for skill in catalog.skills]
    assert len(ids) == len(set(ids)), f'技能 id 重复: {ids}'
    class_ids = {cls.id for cls in catalog.classes}
    for skill in catalog.skills:
        assert skill.class_id in class_ids, f'{skill.id} 引用了不存在的职业 {skill.class_id}'
    print(f'   目录: {len(catalog.classes)} 个职业 / {len(catalog.skills)} 个技能')


def test_catalog_icons_exist():
    """每个技能都要有能读的图标；缺图标会让识别和图标墙都缺一块"""
    catalog = load_catalog()
    for skill in catalog.skills:
        path = catalog.icon_path(skill.id)
        assert path is not None, f'{skill.id}（{skill.name}）缺图标 {skill.icon}'
        icon, mask = load_icon(path)
        assert icon.shape[0] >= 24 and icon.shape[1] >= 24, f'{skill.id} 图标太小: {icon.shape}'
        assert mask.mean() > 0.3, f'{skill.id} 图标有效像素太少: {mask.mean():.2f}'


def test_default_order_covers_all_skills():
    """默认顺序必须正好是全部技能，且按 default_priority 升序"""
    catalog = load_catalog()
    order = catalog.default_order()
    assert len(order) == len(catalog.skills)
    assert set(order) == {skill.id for skill in catalog.skills}
    priorities = [catalog.skill(i).default_priority for i in order]
    assert priorities == sorted(priorities)


# --------------------------------------------------------------------- 归一化
def test_normalize_order_dedup_and_fill():
    """重复的只留第一次出现的位置，没提到的按默认优先级补在最后"""
    catalog = load_catalog()
    ids = catalog_ids()
    order = [ids[-1], ids[-1], ids[0]]
    normalized = catalog.normalize_order(order)
    assert len(normalized) == len(ids)
    assert normalized[0] == ids[-1] and normalized[1] == ids[0]
    assert set(normalized) == set(ids)


def test_normalize_order_drops_unknown_and_disabled():
    """未知 id 丢掉；禁用的技能不出现在释放顺序里"""
    catalog = load_catalog()
    ids = catalog_ids()
    normalized = catalog.normalize_order(
        ['not_a_skill', ids[0], ids[1]], disabled=[ids[1]])
    assert 'not_a_skill' not in normalized
    assert ids[1] not in normalized
    assert normalized[0] == ids[0]
    assert len(normalized) == len(ids) - 1


def test_normalize_disabled_sanitizes():
    """禁用列表去掉未知 id 和重复项"""
    catalog = load_catalog()
    ids = catalog_ids()
    assert catalog.normalize_disabled(['x', ids[0], ids[0]]) == [ids[0]]


# --------------------------------------------------------------------- 识别
def test_identify_placed_skills():
    """贴在技能位上的图标都要被认出来，分数足够高"""
    catalog = load_catalog()
    ids = catalog_ids()
    placements = {0: ids[0], 1: ids[2], 2: ids[-1], 3: ids[1], 4: ids[3]}
    scene = build_scene(placements)
    recognizer = SkillRecognizer.from_catalog(catalog)
    result = recognizer.identify(scene, BATTLE_SKILL_CHECK_REGIONS)

    for slot, skill_id in placements.items():
        match = result[slot]
        assert match.skill_id == skill_id, \
            f'技能{slot + 1} 认成了 {match.skill_id}（应为 {skill_id}，分数 {match.score:.3f}）'
        assert match.score >= 0.9, f'技能{slot + 1} 相似度偏低: {match.score:.3f}'

    # 空技能位（黑底）不该认出任何技能
    for slot in (5, 6):
        assert result[slot].skill_id is None, f'空技能位{slot + 1} 认出了 {result[slot].skill_id}'
        assert result[slot].score < DEFAULT_THRESHOLD


def test_each_icon_matches_itself_best():
    """图标互不混淆：拿自己的图去比，最优匹配必须是它自己"""
    catalog = load_catalog()
    recognizer = SkillRecognizer.from_catalog(catalog)
    confused = []
    for skill in catalog.skills:
        icon, _ = load_icon(catalog.icon_path(skill.id))
        skill_id, score, second = recognizer.match(icon)
        if skill_id != skill.id:
            confused.append((skill.id, skill_id, round(score, 3), round(second, 3)))
    assert not confused, f'图标互相混淆: {confused}'


def test_similarity_is_symmetric_and_bounded():
    """相似度落在 0~1：同一张图 = 1；不同图标要明显低于 1"""
    catalog = load_catalog()
    ids = catalog_ids()
    icon_a, _ = load_icon(catalog.icon_path(ids[0]))
    icon_b, _ = load_icon(catalog.icon_path(ids[1]))
    same = similarity(icon_a, icon_a)
    diff = similarity(icon_a, icon_b)
    assert 0.99 <= same <= 1.0, f'同图相似度异常: {same}'
    assert 0.0 <= diff <= 0.95, f'异图相似度异常: {diff}'
    assert diff < same - 0.1, f'异图相似度 {diff:.3f} 和同图 {same:.3f} 拉不开'


def test_similarity_survives_brightness_gamma_blur():
    """真机截图会有亮度/伽马/模糊差异，这些不能让「自己认不出自己」"""
    import cv2

    catalog = load_catalog()
    ids = catalog_ids()
    recognizer = SkillRecognizer.from_catalog(catalog)
    icon, _ = load_icon(catalog.icon_path(ids[1]))
    variants = {
        '变暗': np.clip(icon * 0.85, 0, 255).astype(np.float32),
        '变亮': np.clip(icon * 1.10, 0, 255).astype(np.float32),
        '伽马': np.clip(255.0 * (np.clip(icon, 0, 255) / 255.0) ** 1.2, 0, 255).astype(np.float32),
        '模糊': cv2.GaussianBlur(icon, (3, 3), 0),
    }
    for name, variant in variants.items():
        skill_id, score, second = recognizer.match(variant)
        assert skill_id == ids[1], f'{name}后认成了 {skill_id}（分数 {score:.3f}）'
        assert score >= 0.85, f'{name}后相似度偏低: {score:.3f}'


def test_ambiguous_icons_are_rejected():
    """两个一模一样的图标 -> 分不出是哪个 -> 判未识别（宁可漏，不可认错）"""
    catalog = load_catalog()
    icon, mask = load_icon(catalog.icon_path(catalog_ids()[0]))
    recognizer = SkillRecognizer({'dup_a': (icon, mask), 'dup_b': (icon, mask)})
    skill_id, score, second = recognizer.match(icon)
    assert skill_id is None, f'歧义图标应该判未识别，却认成了 {skill_id}'
    assert score >= recognizer.threshold and abs(score - second) < recognizer.margin


# --------------------------------------------------------------------- 计划
def test_plan_follows_group_order():
    """计划按预设顺序排，禁用的不点，未识别的排最后"""
    catalog = load_catalog()
    ids = catalog_ids()
    a, b, c = ids[2], ids[0], ids[1]
    # 技能位：1=a 2=b 3=c 4=未识别
    matches = {0: SlotMatch(0, a, 0.95),
               1: SlotMatch(1, b, 0.95),
               2: SlotMatch(2, c, 0.95),
               3: SlotMatch(3, None, 0.30)}
    order = [c, b, a]
    plan = build_plan(matches, order, catalog)

    assert plan_slots(plan) == [2, 1, 0, 3], f'顺序不对: {plan_summary(plan)}'
    assert [e.priority for e in plan[:3]] == [1, 2, 3]
    assert plan[-1].recognized is False and plan[-1].priority == 0


def test_plan_excludes_disabled():
    """禁用的技能即使识别出来也不进计划"""
    catalog = load_catalog()
    ids = catalog_ids()
    matches = {0: SlotMatch(0, ids[0], 0.95),
               1: SlotMatch(1, ids[1], 0.95)}
    plan = build_plan(matches, ids, catalog, disabled=[ids[0]])
    assert plan_slots(plan) == [1], f'禁用没生效: {plan_summary(plan)}'


def test_plan_can_drop_unknown():
    """include_unknown=False 时未识别的技能位不点"""
    catalog = load_catalog()
    ids = catalog_ids()
    matches = {0: SlotMatch(0, None, 0.2), 1: SlotMatch(1, ids[0], 0.9)}
    plan = build_plan(matches, ids, catalog, include_unknown=False)
    assert plan_slots(plan) == [1]


def test_plan_handles_empty_input():
    """没有任何识别结果时返回空计划，不抛异常"""
    catalog = load_catalog()
    assert build_plan({}, catalog.default_order(), catalog) == []
    assert plan_summary([]) == '空'


# --------------------------------------------------------------------- 配置
def test_battle_config_defaults():
    """配置默认值：没有用户预设、通用设置有默认值"""
    from tasks.Battle.config import Battle

    config = Battle()
    assert config.presets == []
    assert config.common.identify_threshold > 0
    assert config.common.respect_cooldown is True
    assert config.find('default') is None, '系统预设不落盘，不该出现在用户预设里'


def test_battle_config_ignores_legacy_group_fields():
    """老版本 config.json 里的 group1/2/3 字段不会让配置加载失败"""
    from tasks.Battle.config import Battle

    config = Battle(**{'group1': {'order': ['whatever'], 'disabled': []}})
    assert config.presets == []
    assert config.common.identify_threshold > 0


def test_battle_config_accepts_user_presets():
    """从 json 里读用户预设：顺序和禁用都保留"""
    from tasks.Battle.config import Battle

    ids = catalog_ids()
    config = Battle(**{'presets': [{'id': 'preset_1', 'name': '打BOSS',
                                    'order': [ids[-1]], 'disabled': [ids[0]]}]})
    preset = config.find('preset_1')
    assert preset is not None and preset.name == '打BOSS'
    assert preset.order == [ids[-1]] and preset.disabled == [ids[0]]


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
