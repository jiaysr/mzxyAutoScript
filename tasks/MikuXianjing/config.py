# This Python file uses the following encoding: utf-8
"""
迷窟仙境 配置（MikuXianjing）

与域外迷窟基本一致（同一个 NPC「迷窟守卫」进入），区别：
- 地图是「迷窟仙境」，初始落点 311,49，打怪基准 308,81；
- 没有时间时段，怪物一直刷新；打怪进度到 100% 收工（按任务成功间隔后再跑）；
- 只打「修仙小妖 / 修仙刀手」；
- 启用后一直打，给更高优先级任务让路。
"""
from pydantic import BaseModel, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.MikuXianjing.config_scheduler import MikuXianjingScheduler


class MikuXianjingBattleConfig(BattleTaskConfig):
    # 连续刷怪场景：目标死后原地快速补锁，减少重新走完整锁敌流程的耗时
    fast_relock: bool = Field(default=True, description='fast_relock_help')
    # 战斗目标白名单：逗号分隔，空 = 不校验目标名
    target_names: str = Field(default='修仙小妖,修仙刀手', description='target_names_help')


class MikuXianjingConfig(BaseModel):
    # NPC 名：小地图右侧 NPC 列表里要点的守卫（与域外迷窟同一个 NPC）
    npc_name: str = Field(default='迷窟守卫', description='小地图 NPC 列表里要点击的 NPC 名')
    # 进入迷窟仙境的 NPC 弹窗选项文案
    enter_text: str = Field(default='进入迷窟仙境', description='NPC 弹窗里的入口选项文案')
    # 传送落点所在地图：先传送到这里，再开小地图点 NPC
    relay_location: str = Field(default='沼泽', description='传送落点所在地图（先传送到这里再开小地图）')
    # NPC 对话弹窗等待超时（秒）
    dialog_timeout: int = Field(default=60, description='NPC 对话弹窗等待超时（秒）')
    # 进入迷窟仙境后等待地图加载的秒数
    enter_wait: int = Field(default=2, description='进入迷窟仙境后等待地图加载（秒）')

    # 打怪基准坐标：进图后先走到这里，被怪追踪跑偏后也要回到这里
    battle_coord: str = Field(default='308,81', description='打怪基准坐标 x,y（进图后站这里，被追踪跑偏后回来）')
    # 基准坐标容差（坐标点）
    battle_tolerance: int = Field(default=2, ge=1, le=10, description='基准坐标容差（坐标点）')

    # 打怪过程中是否定期修理装备（修理时角色站桩不动，修完原地继续打）
    repair_enable: bool = Field(default=True, description='打怪过程中是否定期修理装备')
    # 每打怪多少分钟修理一次
    repair_interval_minutes: int = Field(default=20, ge=5, le=60, description='每打怪多少分钟修理一次装备')


class MikuXianjing(ConfigBase):
    scheduler: MikuXianjingScheduler = Field(default_factory=MikuXianjingScheduler)
    miku_xianjing_config: MikuXianjingConfig = Field(default_factory=MikuXianjingConfig)
    battle_config: MikuXianjingBattleConfig = Field(default_factory=MikuXianjingBattleConfig)