# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.YuwaiMimang.config_scheduler import YuwaiMimangScheduler


class YuwaiBattleConfig(BattleTaskConfig):
    """
    域外迷窟的战斗配置：共享的「战斗预设 + 普通攻击」+ 本任务专属的三项

    - fast_click：秒杀快打，跳过技能识别/冷却判定，锁好目标后按固定顺序连点；
    - fast_relock：目标死后原地补锁（连打场景）。迷窟是「一个坐标周围持续刷怪、
      刷新间隔约 3s、要求在时段内尽量多杀」，所以默认**开**：
      一刀一只时每只都要重锁，完整重锁 ~1.1s、补锁 ~0.3s，不开会白白少杀一半；
    - target_names：只打这份白名单里的怪，锁目标时逐个切换并 OCR 校验名字。

    为什么放在这里而不是塞进共享的 BattleTaskConfig：
    这些开关只对迷窟有意义（单 boss、刷怪间隔 5s+ 的场景用正常流程更合适），
    塞进共享组会让所有任务的设置页都多出用不上的选项。

    `use_normal_attack` 沿用共享默认（关）：本任务不点普通攻击，
    技能全冷却时就等冷却，不做额外补刀。
    """
    # 秒杀快打：不走技能识别 / 冷却判定，锁好目标后固定顺序连点
    fast_click: bool = Field(default=False, description='fast_click_help')
    # 目标死后原地补锁（连续刷怪场景。单 boss / 刷怪间隔长时关掉，走正常流程）
    fast_relock: bool = Field(default=True, description='fast_relock_help')
    # 战斗目标白名单：逗号分隔，空 = 不校验目标名
    target_names: str = Field(default='小鬼,鬼王,鬼将', description='target_names_help')


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
    # 秒杀快打模式下每轮点完攻击后的等待（秒）
    # 按预设模式不用它：那边由「等技能进入冷却」自然控制节奏
    attack_interval: float = Field(default=0.1,
                                   description='秒杀快打每轮点击后的等待（秒）；按预设模式不使用')

    # ---------------- 中途修理 ----------------
    # 打怪过程中是否定期修理装备（修理时角色站桩不动，修完原地继续打）
    repair_enable: bool = Field(default=True, description='打怪过程中是否定期修理装备')
    # 每打怪多少分钟修理一次
    repair_interval_minutes: int = Field(default=20, ge=5, le=60, description='每打怪多少分钟修理一次装备')


class YuwaiMimang(ConfigBase):
    scheduler: YuwaiMimangScheduler = Field(default_factory=YuwaiMimangScheduler)
    yuwai_mimang_config: YuwaiMimangConfig = Field(default_factory=YuwaiMimangConfig)
    battle_config: YuwaiBattleConfig = Field(default_factory=YuwaiBattleConfig)
