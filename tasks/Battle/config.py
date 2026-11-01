# This Python file uses the following encoding: utf-8
"""
战斗配置模型：预设（战略）+ 通用设置

设计要点
--------
- **默认预设（系统预设）不可修改**：它不落盘，由 `presets.default_preset()` 用技能目录的默认优先级生成，
  这样「恢复默认」永远可用，也不会被误改坏；
- 用户预设存 `config/<实例>.json` 的 `battle.presets` 里，每个实例（账号）各一套；
- 创建预设时可以「基于某个预设」复制顺序和禁用，再自己改；
- **没有「全局生效预设」**：哪个任务用哪套预设，由任务自己的「战斗配置」组决定
  （`tasks/Component/battle_config.py`，默认系统预设）。

每个预设都描述**目录里的全部技能**：`order` 是完整顺序（含被禁用的，位置不丢），
`disabled` 是本预设里不释放的技能。执行时 = order 去掉 disabled。
"""
from typing import List, Optional

from pydantic import BaseModel, Field

from tasks.Battle.catalog import load_catalog
from tasks.Component.config_base import ConfigBase

# 默认预设的 id / 名称（内建，不落盘、不可修改）
DEFAULT_PRESET_ID = 'default'
DEFAULT_PRESET_NAME = '默认预设'
# 预设数量上限（防止配置膨胀、UI 卡死）
MAX_PRESETS = 20
# 预设名长度上限
MAX_PRESET_NAME_LENGTH = 24


def _default_order() -> List[str]:
    """默认顺序（目录加载失败时返回空表，读取端会用 normalize_order 补齐）"""
    try:
        return load_catalog().default_order()
    except Exception:  # noqa: BLE001 - 配置模型不能因为目录有问题就导入失败
        return []


_DEFAULT_ORDER = _default_order()


class BattlePreset(BaseModel):
    """一个战斗预设（用户可建多个）"""
    id: str = Field(default='preset_1', description='预设 id，创建后不要改')
    name: str = Field(default='预设1', description='预设名称（随便起，比如「日常清怪」「打 BOSS」）')
    order: List[str] = Field(default=list(_DEFAULT_ORDER),
                             description='释放顺序：技能 id 列表，越靠前优先级越高')
    disabled: List[str] = Field(default=[], description='本预设禁用的技能 id')


class BattleCommon(BaseModel):
    """战斗通用设置（不区分预设、全局唯一）"""
    identify_threshold: float = Field(default=0.78, ge=0.2, le=0.99,
                                      description='技能图标识别相似度下限，低于该值视为未识别')
    respect_cooldown: bool = Field(default=True, description='只点冷却好的技能')
    cast_interval: float = Field(default=0.2, ge=0.0, le=3.0,
                                 description='两次技能点击之间的间隔（秒）')


class Battle(ConfigBase):
    """战斗菜单的配置（不进调度器，不是任务）"""
    common: BattleCommon = Field(default_factory=BattleCommon)
    # 只存用户预设；默认预设在代码里（见 presets.default_preset）
    presets: List[BattlePreset] = Field(default_factory=list, description='用户自定义的战斗预设')

    def find(self, preset_id: str) -> Optional[BattlePreset]:
        """按 id 找用户预设（默认预设返回 None，它不在这里）"""
        for preset in self.presets:
            if preset.id == preset_id:
                return preset
        return None

    def preset_ids(self) -> List[str]:
        return [preset.id for preset in self.presets]
