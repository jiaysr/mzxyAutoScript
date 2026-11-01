# This Python file uses the following encoding: utf-8
"""
任务配置里的「战斗配置」组

所有会打怪的任务共用这一个组：里面只有一个字段 `battle_preset`（战斗预设下拉框），
默认就是系统预设（`default`）。任务执行时 `SimpleBattle.battle_preset()` 会读它。

用法（任务的 config.py）：

    from tasks.Component.battle_config import BattleTaskConfig

    class SomeTask(ConfigBase):
        scheduler: Scheduler = Field(default_factory=Scheduler)
        battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)
        ...

这样 arg 页面会多出一个「战斗配置」卡片，里面只有「战斗预设」一项。
"""
from pydantic import BaseModel, Field

from tasks.Battle.config import DEFAULT_PRESET_ID
from tasks.Component.config_base import BattlePresetName


class BattleTaskConfig(BaseModel):
    """任务用哪套战斗预设 + 是否点普通攻击"""
    battle_preset: BattlePresetName = Field(default=DEFAULT_PRESET_ID,
                                            description='battle_preset_help')
    use_normal_attack: bool = Field(default=False,
                                    description='use_normal_attack_help')
