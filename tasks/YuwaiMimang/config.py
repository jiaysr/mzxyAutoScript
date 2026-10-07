# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

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
    # 可锁定的怪物名，英文逗号分隔
    monster_names: str = Field(default='小鬼,鬼将,鬼王', description='可锁定的怪物名（英文逗号分隔）')
    # 需要打几下才死的怪物名（小鬼/鬼将一下秒杀，鬼王要两下）
    boss_monster: str = Field(default='鬼王', description='需要普通攻击打两下的怪物名')
    boss_hits: int = Field(default=2, description='boss_monster 需要普通攻击的次数')
    # 普通攻击与技能1的冷却间隔（秒），游戏里两者都在 1 秒以上
    attack_interval: float = Field(default=1.5, description='攻击间隔（秒），普通攻击与技能1的冷却都在1秒以上')
    # 连续多少次锁定不到目标就报错
    max_lock_fail: int = Field(default=10, description='连续锁定不到目标多少次后报错')
    # 单轮打怪的整体超时（秒）
    battle_timeout: int = Field(default=600, description='单轮打怪整体超时（秒）')
    # 回基准坐标的超时（秒）；超时也要继续打，不阻塞攻击
    recover_timeout: int = Field(default=8, description='回基准坐标的超时（秒），超时后继续打怪')


class YuwaiMimang(ConfigBase):
    scheduler: YuwaiMimangScheduler = Field(default_factory=YuwaiMimangScheduler)
    yuwai_mimang_config: YuwaiMimangConfig = Field(default_factory=YuwaiMimangConfig)