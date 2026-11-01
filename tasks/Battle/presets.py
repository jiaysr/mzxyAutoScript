# This Python file uses the following encoding: utf-8
"""
预设的读/建/改/删（纯逻辑，API 和任务共用）

为什么默认预设不落盘
--------------------
「默认预设」= 技能目录里的 default_priority 顺序，禁用一个都不禁用。
它不写进 config json，好处是：
- 永远不会被手滑改坏，界面上也能明确显示「不可修改」；
- 技能目录更新（新增技能）后，默认预设自动跟着变，不用迁移配置；
- 删除所有用户预设后仍然有东西可用。

用户预设基于某个预设创建（`create_preset(base=...)`），
复制的是当时的 order / disabled，之后互不影响。
"""
from typing import Dict, List, Optional

from module.logger import logger
from tasks.Battle.catalog import SkillCatalog, load_catalog
from tasks.Battle.config import (DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME, MAX_PRESET_NAME_LENGTH,
                                 MAX_PRESETS, Battle, BattlePreset)


class PresetError(ValueError):
    """预设操作失败（名称为空、数量超限、改默认预设等），API 层转成 400"""


# --------------------------------------------------------------------- 读
def default_preset(catalog: SkillCatalog = None) -> Dict:
    """内建默认预设（不可修改）"""
    catalog = catalog or load_catalog()
    return {
        'id': DEFAULT_PRESET_ID,
        'name': DEFAULT_PRESET_NAME,
        'order': catalog.default_order(),
        'disabled': [],
        'builtin': True,
    }


def preset_to_dict(preset: BattlePreset, catalog: SkillCatalog = None) -> Dict:
    """用户预设 -> API/UI 用的 dict（顺序补齐成 20 条，含被禁用的）"""
    catalog = catalog or load_catalog()
    return {
        'id': preset.id,
        'name': preset.name,
        'order': catalog.complete_order(preset.order),
        'disabled': catalog.normalize_disabled(preset.disabled),
        'builtin': False,
    }


def list_presets(battle: Battle, catalog: SkillCatalog = None) -> List[Dict]:
    """默认预设 + 全部用户预设（默认排第一个）"""
    catalog = catalog or load_catalog()
    return [default_preset(catalog)] + [preset_to_dict(p, catalog) for p in battle.presets]


def resolve_preset(battle: Battle, preset_id: str,
                   catalog: SkillCatalog = None) -> Dict:
    """
    按 id 取预设，找不到（被删/写错/旧配置）自动回落到默认预设
    """
    catalog = catalog or load_catalog()
    if not preset_id or preset_id == DEFAULT_PRESET_ID:
        return default_preset(catalog)
    preset = battle.find(preset_id)
    if preset is None:
        logger.warning(f'预设 {preset_id} 不存在，回落到默认预设')
        return default_preset(catalog)
    return preset_to_dict(preset, catalog)


# --------------------------------------------------------------------- 写
def next_preset_id(battle: Battle) -> str:
    """生成不重复的预设 id：preset_1、preset_2 ..."""
    used = set(battle.preset_ids())
    index = 1
    while f'preset_{index}' in used:
        index += 1
    return f'preset_{index}'


def clean_name(name: str) -> str:
    name = (name or '').strip()
    if not name:
        raise PresetError('预设名称不能为空')
    return name[:MAX_PRESET_NAME_LENGTH]


def create_preset(battle: Battle, catalog: SkillCatalog = None,
                  name: str = '', base: str = DEFAULT_PRESET_ID) -> BattlePreset:
    """
    新建预设：基于 `base` 复制顺序和禁用

    :param name: 预设名，留空自动取「预设N」
    :param base: 复制哪个预设（默认预设 / 其他用户预设的 id）
    """
    catalog = catalog or load_catalog()
    if len(battle.presets) >= MAX_PRESETS:
        raise PresetError(f'预设最多 {MAX_PRESETS} 个')

    source = resolve_preset(battle, base, catalog)
    preset_id = next_preset_id(battle)
    if not (name or '').strip():
        name = f'预设{len(battle.presets) + 1}'
    preset = BattlePreset(
        id=preset_id,
        name=clean_name(name),
        order=catalog.complete_order(source.get('order')),
        disabled=catalog.normalize_disabled(source.get('disabled')),
    )
    battle.presets.append(preset)
    logger.info(f'新建战斗预设 {preset.id}（{preset.name}），基于 {base}')
    return preset


def update_preset(battle: Battle, preset_id: str, catalog: SkillCatalog = None,
                  name: Optional[str] = None, order: Optional[List[str]] = None,
                  disabled: Optional[List[str]] = None) -> BattlePreset:
    """
    修改用户预设（默认预设不可改）

    :param order: 传了就整体替换（补齐成 20 条，含禁用的）
    :param disabled: 传了就整体替换
    """
    catalog = catalog or load_catalog()
    if preset_id == DEFAULT_PRESET_ID:
        raise PresetError('默认预设不可修改，请先「基于默认预设新建」一个')
    preset = battle.find(preset_id)
    if preset is None:
        raise PresetError(f'预设 {preset_id} 不存在')

    if name is not None:
        preset.name = clean_name(name)
    if order is not None:
        preset.order = catalog.complete_order(order)
    if disabled is not None:
        preset.disabled = catalog.normalize_disabled(disabled)
    return preset


def delete_preset(battle: Battle, preset_id: str) -> None:
    """删除用户预设（默认预设不可删）"""
    if preset_id == DEFAULT_PRESET_ID:
        raise PresetError('默认预设不可删除')
    preset = battle.find(preset_id)
    if preset is None:
        raise PresetError(f'预设 {preset_id} 不存在')
    battle.presets = [p for p in battle.presets if p.id != preset_id]
    logger.info(f'删除战斗预设 {preset_id}')


def reset_preset(battle: Battle, preset_id: str,
                 catalog: SkillCatalog = None) -> None:
    """把某个用户预设的顺序恢复成默认顺序、清空禁用"""
    catalog = catalog or load_catalog()
    update_preset(battle, preset_id, catalog,
                  order=catalog.default_order(), disabled=[])
