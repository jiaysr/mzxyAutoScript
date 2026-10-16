# This Python file uses the following encoding: utf-8
"""
副本玩法 配置（Dungeon）

一个任务里跑多个不同的副本：每个具体副本的开关/参数在这里逐项添加，
命名约定 <副本名>_enable / <副本名>_xxx。
"""
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class DungeonConfig(BaseModel):
    """
    副本玩法通用参数

    具体副本（tasks/Dungeon/dungeons/<Name>.py）的开关/参数在此逐项添加，
    例：`<name>_enable: bool`、`<name>_count: int` 等。
    """
    # 副本之间执行的间隔（秒），避免连续切图太快
    dungeon_interval: int = Field(default=3, ge=0, le=60,
                                  description='dungeon_interval_help')


class Dungeon(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    dungeon_config: DungeonConfig = Field(default_factory=DungeonConfig)
