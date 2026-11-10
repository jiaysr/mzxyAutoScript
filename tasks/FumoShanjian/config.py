# This Python file uses the following encoding: utf-8
"""
伏魔山涧 配置（FumoShanjian）

当前进度：通过万剑冢的伏魔大使 NPC 打开对话，OCR 找到「进入伏魔涧」并点击，
直接进入副本地图（不需要再点「确定」）。副本内按小地图打怪点自动寻路，
锁定可攻击目标后按「战斗配置」的预设释放技能。
"""
from enum import Enum

from pydantic import ConfigDict, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class FumoTaskMode(str, Enum):
    """任务方式：决定本次要不要进副本、进副本后打到什么程度"""
    # 一直继续，直到副本正常结束（不看活跃任务）
    ALWAYS = 'always'
    # 只完成活跃任务：活动-活跃页里「伏魔山涧」已完成就直接收工
    ACTIVE_ONLY = 'active_only'


class FumoShanjianConfig(ConfigBase):
    """伏魔山涧参数"""
    # script_set_arg 用 setattr 写配置，pydantic 默认不校验赋值，
    # 不开这项 ge/le 会被绕过
    model_config = ConfigDict(validate_assignment=True)

    task_mode: FumoTaskMode = Field(default=FumoTaskMode.ACTIVE_ONLY,
                                    description='fumo_shanjian_task_mode_help')
    npc_wait_timeout: int = Field(default=20, ge=5, le=120,
                                  description='点击小地图 NPC 后等待「伏魔大使」对话弹窗出现的超时（秒）')
    enter_option: str = Field(default='进入伏魔涧',
                              description='伏魔大使对话框里进入副本的选项文字（OCR 定位后点击）')
    enter_wait: int = Field(default=20, ge=5, le=120,
                            description='点「进入伏魔涧」后等待地点切换到副本的超时（秒）')


class FumoShanjian(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    fumo_shanjian_config: FumoShanjianConfig = Field(default_factory=FumoShanjianConfig)
    # 副本内打怪用的战斗预设 / 普通攻击开关
    battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)
