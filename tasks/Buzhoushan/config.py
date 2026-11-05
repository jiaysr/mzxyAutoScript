# This Python file uses the following encoding: utf-8
"""
不周山 配置（Buzhoushan）

- challenge_count：挑战次数。0 表示一直挑战到剩余次数为 0；
  设置值超过可挑战次数时，按最大可挑战次数执行。
- layer_range：挑战层数区间（每 10 层一档：1-10 ... 101-110）。
"""
from enum import Enum

from pydantic import ConfigDict, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class LayerRange(str, Enum):
    """挑战层数区间（每 10 层一档）"""
    L1_10 = '1-10'
    L11_20 = '11-20'
    L21_30 = '21-30'
    L31_40 = '31-40'
    L41_50 = '41-50'
    L51_60 = '51-60'
    L61_70 = '61-70'
    L71_80 = '71-80'
    L81_90 = '81-90'
    L91_100 = '91-100'
    L101_110 = '101-110'


class BuzhoushanConfig(ConfigBase):
    """不周山参数"""
    # script_set_arg 用 setattr 写配置，pydantic 默认不校验赋值，
    # 不开这项 ge/le 会被绕过（次数能存进负数）
    model_config = ConfigDict(validate_assignment=True)

    challenge_count: int = Field(default=0, ge=0, le=999,
                                 description='挑战次数：0=挑战到剩余次数为 0；'
                                             '设置值超过可挑战次数时，按最大可挑战次数执行')
    layer_range: LayerRange = Field(default=LayerRange.L1_10,
                                    description='挑战层数区间（1-10 ... 101-110）')


class BuzhoushanBattleConfig(BattleTaskConfig):
    """
    不周山的战斗配置：共享的「战斗预设 + 普通攻击」+ 本任务专属的补锁开关

    - fast_relock：目标死后原地补锁。不周山一层 4~5 只怪、一刀一只，
      每只都走完整锁目标约 1.1s，补锁一次约 0.45s，所以默认**开**；
    - 但每层最后一只是没有怪可补的，所以任务里补锁前先判「本层是否已清完」
      （小地图出传送点）和「是否已出结算弹窗」，并且只补 1 次就交回外层，
      否则空切会白赔 1s+/层（见 ScriptTask.battle_relock_after_kill）。

    为什么放在这里而不是塞进共享的 BattleTaskConfig：
    「刷完一波就没了」和「持续刷怪」两种场景的取舍相反，塞进共享组会让
    所有任务的设置页都多出用不上的选项（迷窟的做法同理）。
    """
    # 目标死后原地补锁（连续刷怪场景。单 boss / 刷怪间隔长时关掉，走正常流程）
    fast_relock: bool = Field(default=True, description='fast_relock_help')


class Buzhoushan(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    buzhoushan_config: BuzhoushanConfig = Field(default_factory=BuzhoushanConfig)
    battle_config: BuzhoushanBattleConfig = Field(default_factory=BuzhoushanBattleConfig)
