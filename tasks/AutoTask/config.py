# This Python file uses the following encoding: utf-8
from enum import Enum

from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class BonusMultiplier(str, Enum):
    """
    任务加量：该任务是否使用加量卡（任务加量页的 3 倍 / 5 倍加量卡）
    """

    NONE = 'none'  # 不加量
    X3 = 'bonus_3x'  # 3 倍
    X5 = 'bonus_5x'  # 5 倍


class AutoTaskConfig(BaseModel):
    # 以下 6 个任务支持任务加量，各自独立选择（其他任务不吃加量卡，不设选项）
    xiuzhen_bonus: BonusMultiplier = Field(default=BonusMultiplier.NONE,
                                           description='auto_task_xiuzhen_bonus_help')
    jinqian_bonus: BonusMultiplier = Field(default=BonusMultiplier.NONE,
                                           description='auto_task_jinqian_bonus_help')
    jineng_bonus: BonusMultiplier = Field(default=BonusMultiplier.NONE,
                                          description='auto_task_jineng_bonus_help')
    jingyan_bonus: BonusMultiplier = Field(default=BonusMultiplier.NONE,
                                           description='auto_task_jingyan_bonus_help')
    zuoqi_bonus: BonusMultiplier = Field(default=BonusMultiplier.NONE,
                                         description='auto_task_zuoqi_bonus_help')
    xianmeng_bonus: BonusMultiplier = Field(default=BonusMultiplier.NONE,
                                            description='auto_task_xianmeng_bonus_help')


class AutoTask(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    auto_task_config: AutoTaskConfig = Field(default_factory=AutoTaskConfig)
