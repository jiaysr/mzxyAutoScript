# This Python file uses the following encoding: utf-8
"""
副本玩法 配置（Dungeon）

一个任务里跑多个不同的副本：每个具体副本的开关/参数在这里逐项添加，
命名约定 <副本名>_enable / <副本名>_xxx。
"""
from enum import Enum

from pydantic import ConfigDict, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class PrivilegeDungeon(str, Enum):
    """特权额外挑战副本（只能选已勾选且次数=2的副本）"""
    NONE = 'none'
    SHAYU_MULTI = 'shayu_multi'
    SHAYU_SOLO = 'shayu_solo'
    DIYULING_MULTI = 'diyuling_multi'
    DIYULING_SOLO = 'diyuling_solo'
    LIULI_HUANJING = 'liuli_huanjing'


class DungeonConfig(ConfigBase):
    """
    副本玩法通用参数 + 各副本开关/次数

    具体副本（tasks/Dungeon/dungeons/<Name>.py）在 DUNGEONS 登记表里登记，
    开关字段命名 <副本名>_enable、次数字段命名 <副本名>_count。
    """
    # script_set_arg 用 setattr 写配置，pydantic 默认不校验赋值，
    # 不开这项 ge/le 会被绕过（次数能存进 0 / 5）
    model_config = ConfigDict(validate_assignment=True)

    # 副本之间执行的间隔（秒），避免连续切图太快
    dungeon_interval: int = Field(default=3, ge=0, le=60,
                                  description='dungeon_interval_help')

    # 特权额外挑战副本：正常次数用完后，用特权次数继续刷这个副本
    privilege_dungeon: PrivilegeDungeon = Field(
        default=PrivilegeDungeon.NONE,
        description='特权额外挑战副本：只能选已勾选且次数=2的副本；'
                    '该副本正常次数用完后，用特权次数继续挑战，直到特权次数为 0',
        json_schema_extra={'enum_filter': 'dungeon_privilege'},
    )

    # 沙狱
    shayu_multi_enable: bool = Field(default=False, description='是否挑战沙狱[多人]')
    shayu_multi_count: int = Field(default=2, ge=1, le=2, description='沙狱[多人]每天挑战次数（游戏内最多 2 次）')
    shayu_solo_enable: bool = Field(default=False, description='是否挑战沙狱[单人]')
    shayu_solo_count: int = Field(default=2, ge=1, le=2, description='沙狱[单人]每天挑战次数（游戏内最多 2 次）')

    # 帝玉岭
    diyuling_multi_enable: bool = Field(default=False, description='是否挑战帝玉岭[多人]')
    diyuling_multi_count: int = Field(default=2, ge=1, le=2, description='帝玉岭[多人]每天挑战次数（游戏内最多 2 次）')
    diyuling_solo_enable: bool = Field(default=False, description='是否挑战帝玉岭[单人]')
    diyuling_solo_count: int = Field(default=2, ge=1, le=2, description='帝玉岭[单人]每天挑战次数（游戏内最多 2 次）')

    # 琉璃幻境
    liuli_huanjing_enable: bool = Field(default=False, description='是否挑战琉璃幻境')
    liuli_huanjing_count: int = Field(default=2, ge=1, le=2, description='琉璃幻境每天挑战次数（游戏内最多 2 次）')


class Dungeon(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    dungeon_config: DungeonConfig = Field(default_factory=DungeonConfig)
    battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)
