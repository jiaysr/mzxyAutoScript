# This Python file uses the following encoding: utf-8
"""
限时活动-野外雪人 配置（WildSnowman）
"""
from pydantic import BaseModel, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class WildSnowmanScheduler(Scheduler):
    """野外雪人优先级最低，只作为空闲时的持续挂机任务"""
    priority: int = Field(default=100, description='priority_help')


class WildSnowmanBattleConfig(BattleTaskConfig):
    # 连续刷怪场景：目标死后原地快速补锁，减少重新走完整锁敌流程的耗时
    fast_relock: bool = Field(default=True, description='fast_relock_help')


class WildSnowmanConfig(BaseModel):
    # 打怪基准坐标：进图/复活后先走到这里
    battle_coord: str = Field(default='251,175', description='打怪基准坐标 x,y（角色站这里循环打怪）')
    # 基准坐标容差
    battle_tolerance: int = Field(default=5, ge=1, le=10, description='打怪基准坐标容差')
    # 每打怪多少分钟治疗并修理一次
    heal_repair_interval_minutes: int = Field(
        default=40, ge=5, le=240,
        description='每打怪多少分钟治疗并修理一次（死亡复活后重新计时，掉线重启不重置）')
    # 战斗目标白名单：逗号分隔，兼容简繁体
    target_names: str = Field(
        default='陵鱼,陵魚,可爱雪人,可愛雪人',
        description='只打这些目标，逗号分隔（陵鱼,陵魚,可爱雪人,可愛雪人）')


class WildSnowman(ConfigBase):
    scheduler: WildSnowmanScheduler = Field(default_factory=WildSnowmanScheduler)
    wild_snowman_config: WildSnowmanConfig = Field(default_factory=WildSnowmanConfig)
    battle_config: WildSnowmanBattleConfig = Field(default_factory=WildSnowmanBattleConfig)
