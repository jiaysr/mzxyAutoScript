# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class WorldBossConfig(BaseModel):
    # 提前多少时间出发前往首领位置
    advance_time: Time = Field(default=Time(minute=5), description='world_boss_advance_time_help')
    # 锁定目标时用于匹配的目标名称，多个用英文逗号分隔；留空则使用首领名称
    target_name: str = Field(default='', description='world_boss_target_name_help')


class ManchuiConfig(BaseModel):
    time_09: bool = Field(default=False, title='09:00', description='world_boss_manchui_09_help')
    time_11: bool = Field(default=False, title='11:00', description='world_boss_manchui_11_help')
    time_13: bool = Field(default=False, title='13:00', description='world_boss_manchui_13_help')


class WuqingConfig(BaseModel):
    time_10: bool = Field(default=False, title='10:00', description='world_boss_wuqing_10_help')
    time_12: bool = Field(default=False, title='12:00', description='world_boss_wuqing_12_help')
    time_19: bool = Field(default=False, title='19:00', description='world_boss_wuqing_19_help')


class JulianConfig(BaseModel):
    time_15: bool = Field(default=False, title='15:00', description='world_boss_julian_15_help')
    time_17: bool = Field(default=False, title='17:00', description='world_boss_julian_17_help')
    time_19: bool = Field(default=False, title='19:00', description='world_boss_julian_19_help')


class FuwangConfig(BaseModel):
    time_14: bool = Field(default=False, title='14:00', description='world_boss_fuwang_14_help')
    time_18: bool = Field(default=False, title='18:00', description='world_boss_fuwang_18_help')
    time_22: bool = Field(default=False, title='22:00', description='world_boss_fuwang_22_help')


class XiewangConfig(BaseModel):
    time_21: bool = Field(default=False, title='21:00', description='world_boss_xiewang_21_help')


class WorldBoss(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    world_boss_config: WorldBossConfig = Field(default_factory=WorldBossConfig)
    manchui: ManchuiConfig = Field(default_factory=ManchuiConfig)
    wuqing: WuqingConfig = Field(default_factory=WuqingConfig)
    julian: JulianConfig = Field(default_factory=JulianConfig)
    fuwang: FuwangConfig = Field(default_factory=FuwangConfig)
    xiewang: XiewangConfig = Field(default_factory=XiewangConfig)
