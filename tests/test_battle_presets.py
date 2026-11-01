# -*- coding: utf-8 -*-
"""
战斗预设的增删改查单测（纯逻辑，不连设备、不碰文件）

覆盖：
- 系统预设：内建、只读、覆盖全部技能、按目录默认优先级
- 列表：默认预设排第一
- 新建：基于默认/其他预设复制、自动 id、自动起名、数量上限、空名拒绝
- 修改：默认预设拒绝、order 归一化（去重/未知 id/补齐）
- 删除：删当前生效预设要回落到默认
- 切换生效预设：不存在的 id 拒绝
- 恢复默认顺序

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_presets.py
"""
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.catalog import load_catalog
from tasks.Battle.config import DEFAULT_PRESET_ID, MAX_PRESETS, Battle
from tasks.Battle.presets import (PresetError, create_preset, default_preset, delete_preset,
                                  list_presets, next_preset_id, reset_preset,
                                  resolve_preset, update_preset)


def fresh() -> Battle:
    """一份干净配置（没有用户预设）"""
    return Battle()


def skill_ids():
    """技能目录里的全部 id（测试不要写死具体技能，换技能数据不用改测试）"""
    return [skill.id for skill in load_catalog().skills]


# --------------------------------------------------------------------- 默认预设
def test_default_preset_is_builtin_and_complete():
    """系统预设：内建、覆盖全部技能、没有禁用、顺序 = 目录默认顺序"""
    catalog = load_catalog()
    preset = default_preset(catalog)
    assert preset['id'] == DEFAULT_PRESET_ID
    assert preset['builtin'] is True
    assert preset['disabled'] == []
    assert preset['order'] == catalog.default_order()
    assert len(preset['order']) == len(catalog.skills)


def test_default_preset_never_appears_in_user_presets():
    """默认预设不落盘：用户预设列表里不会出现 id=default"""
    battle = fresh()
    assert battle.find(DEFAULT_PRESET_ID) is None
    assert DEFAULT_PRESET_ID not in battle.preset_ids()


def test_list_presets_default_first():
    """列表接口：默认预设永远排第一个"""
    battle = fresh()
    create_preset(battle, name='甲')
    create_preset(battle, name='乙')
    presets = list_presets(battle)
    assert [p['id'] for p in presets] == ['default', 'preset_1', 'preset_2']
    assert presets[0]['builtin'] is True
    assert presets[1]['builtin'] is False


# --------------------------------------------------------------------- 新建
def test_create_preset_copies_base():
    """新建时复制 base 的顺序和禁用，之后互不影响"""
    battle = fresh()
    catalog = load_catalog()
    ids = skill_ids()
    base = create_preset(battle, name='打BOSS')
    update_preset(battle, base.id, order=[ids[-1], ids[-2]], disabled=[ids[-2]])

    copied = create_preset(battle, name='打BOSS-复制', base=base.id)
    assert copied.order[:2] == [ids[-1], ids[-2]]
    assert copied.disabled == [ids[-2]]

    # 改复制出来的预设不影响原预设
    update_preset(battle, copied.id, disabled=[])
    assert base.disabled == [ids[-2]]


def test_create_preset_auto_id_and_name():
    """自动 id 递增、名称为空时自动起名"""
    battle = fresh()
    first = create_preset(battle)
    second = create_preset(battle)
    assert first.id == 'preset_1' and first.name == '预设1'
    assert second.id == 'preset_2' and second.name == '预设2'
    assert next_preset_id(battle) == 'preset_3'


def test_create_preset_auto_names_blank_input():
    """名称是空白时自动起名（界面里直接点确定也不会出错）"""
    battle = fresh()
    preset = create_preset(battle, name='   ')
    assert preset.name == '预设1'


def test_create_preset_limit():
    """超过数量上限要拒绝"""
    battle = fresh()
    while len(battle.presets) < MAX_PRESETS:
        create_preset(battle)
    try:
        create_preset(battle)
        raise AssertionError('超过上限没有报错')
    except PresetError:
        pass


def test_clean_name_truncates():
    """名称过长会被截断，不会撑爆界面"""
    battle = fresh()
    preset = create_preset(battle, name='很长的名字' * 20)
    assert len(preset.name) <= 24


# --------------------------------------------------------------------- 修改
def test_update_default_preset_is_rejected():
    """默认预设不可修改"""
    battle = fresh()
    try:
        update_preset(battle, DEFAULT_PRESET_ID, name='改名')
        raise AssertionError('默认预设竟然能改')
    except PresetError as e:
        assert '默认预设' in str(e)


def test_update_order_is_normalized():
    """改顺序时未知 id 丢掉、重复的只留一次、缺的按默认优先级补到 20 条"""
    battle = fresh()
    preset = create_preset(battle)
    ids = skill_ids()
    update_preset(battle, preset.id, order=['not_a_skill', ids[0], ids[0]])
    assert preset.order[0] == ids[0]
    assert 'not_a_skill' not in preset.order
    assert len(preset.order) == len(ids)
    assert preset.order.count(ids[0]) == 1


def test_update_unknown_preset_is_rejected():
    battle = fresh()
    try:
        update_preset(battle, 'preset_99', name='x')
        raise AssertionError('不存在的预设竟然能改')
    except PresetError:
        pass


# --------------------------------------------------------------------- 删除 / 生效
def test_delete_preset_removes_only_that_one():
    """删除预设只删指定的那个，其他预设和系统预设不受影响"""
    battle = fresh()
    first = create_preset(battle, name='甲')
    second = create_preset(battle, name='乙')
    delete_preset(battle, first.id)
    assert [p.id for p in battle.presets] == [second.id]
    assert resolve_preset(battle, first.id)['id'] == DEFAULT_PRESET_ID


def test_delete_default_is_rejected():
    battle = fresh()
    try:
        delete_preset(battle, DEFAULT_PRESET_ID)
        raise AssertionError('默认预设竟然能删')
    except PresetError:
        pass


def test_resolve_unknown_preset_falls_back():
    """配置里的预设 id 失效（被删/写错）时回落系统预设，而不是报错"""
    battle = fresh()
    preset = resolve_preset(battle, 'preset_99')
    assert preset['id'] == DEFAULT_PRESET_ID
    assert resolve_preset(battle, '')['id'] == DEFAULT_PRESET_ID


def test_reset_preset_restores_default_order():
    """恢复默认顺序：顺序回目录默认、禁用清空"""
    battle = fresh()
    catalog = load_catalog()
    preset = create_preset(battle)
    ids = skill_ids()
    update_preset(battle, preset.id, order=[ids[-1]], disabled=[ids[0]])

    reset_preset(battle, preset.id)
    assert preset.order == catalog.default_order()
    assert preset.disabled == []


def test_preset_to_dict_is_complete():
    """给界面的预设数据永远是 20 条（含被禁用的，位置不丢）"""
    battle = fresh()
    catalog = load_catalog()
    preset = create_preset(battle)
    ids = skill_ids()
    update_preset(battle, preset.id, order=[ids[-1]], disabled=[ids[-1]])
    data = [p for p in list_presets(battle, catalog) if p['id'] == preset.id][0]
    assert len(data['order']) == len(ids)
    assert data['order'][0] == ids[-1]
    assert data['disabled'] == [ids[-1]]


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
