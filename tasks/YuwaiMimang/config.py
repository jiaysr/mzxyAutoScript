# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.YuwaiMimang.config_scheduler import YuwaiMimangScheduler


class YuwaiMimangConfig(BaseModel):
    # NPC 名：小地图右侧 NPC 列表里要点的守卫
    npc_name: str = Field(default='迷窟守卫', description='小地图 NPC 列表里要点击的 NPC 名')
    # 进入迷窟的 NPC 名字在弹窗里的选项文案
    enter_text: str = Field(default='进入域外迷窟', description='NPC 弹窗里的入口选项文案')
    # 传送落点所在地图：先传送到这里，再开小地图点 NPC
    relay_location: str = Field(default='沼泽', description='传送落点所在地图（先传送到这里再开小地图）')
    # 怪物刷新时间段，格式 HH:MM-HH:MM，多个用英文逗号分隔
    monster_times: str = Field(default='10:30-11:30,15:30-16:30',
                               description='怪物刷新时间段（HH:MM-HH:MM，多个用逗号分隔）')
    # 提前准备时间：提前这么久把游戏准备到主页面
    advance_time: Time = Field(default=Time(minute=3), description='提前准备时间，提前这么久把游戏准备到主页面')
    # NPC 对话弹窗等待超时（秒）
    dialog_timeout: int = Field(default=60, description='NPC 对话弹窗等待超时（秒）')
    # 进入迷窟后等待地图加载的秒数
    enter_wait: int = Field(default=2, description='进入迷窟后等待地图加载（秒）')

    # ---------------- 打怪 ----------------
    # 打怪基准坐标：进图后先走到这里，被怪追踪跑偏后也要回到这里
    battle_coord: str = Field(default='304,76', description='打怪基准坐标 x,y（进图后站这里，被追踪跑偏后回来）')
    # 基准坐标容差（坐标点）
    battle_tolerance: int = Field(default=2, description='基准坐标容差（坐标点，最大容错 2）')
    # 每轮点完「目标」和三个攻击按钮后的等待（秒）
    # 秒杀账号下不需要等冷却：点目标 -> 普攻/技能1/技能3 各点一遍 -> 停一下 -> 下一轮
    attack_interval: float = Field(default=0.1, description='每轮点「目标」后和三个攻击点完后的等待（秒）')

    # ---------------- 中途修理 ----------------
    # 打怪过程中是否定期修理装备（修理时角色站桩不动，修完原地继续打）
    repair_enable: bool = Field(default=True, description='打怪过程中是否定期修理装备')
    # 每打怪多少分钟修理一次
    repair_interval_minutes: int = Field(default=20, ge=5, le=60, description='每打怪多少分钟修理一次装备')


class YuwaiMimang(ConfigBase):
    scheduler: YuwaiMimangScheduler = Field(default_factory=YuwaiMimangScheduler)
    yuwai_mimang_config: YuwaiMimangConfig = Field(default_factory=YuwaiMimangConfig)
    battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)