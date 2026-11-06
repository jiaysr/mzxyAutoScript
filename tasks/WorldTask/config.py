# This Python file uses the following encoding: utf-8
from enum import Enum

from pydantic import BaseModel, Field

from tasks.AutoTask.config import BonusMultiplier
from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class DropFarmTask(str, Enum):
    """世界之心刷怪点：借哪个打怪任务的位置刷掉落"""
    GLORY = '为了荣耀'
    BEAST = '兽毛之争'
    GOLDEN = '黄金比例'


class WorldTaskConfig(BaseModel):
    # 接任务时用哪种加量卡（任务加量页的 3 倍 / 5 倍卡）；none = 不加量
    # 卡不够 7 张（=要接的任务数）就跳过加量，直接接任务
    bonus_multiplier: BonusMultiplier = Field(
        default=BonusMultiplier.NONE,
        title='加量卡',
        description='接取跨服任务时使用的加量卡（任务加量页的 3 倍 / 5 倍卡，'
                    '不足 7 张会自动跳过）')
    # 世界之心没有自己的玩法，靠打怪掉落：在这里选用哪个打怪任务的位置刷
    drop_farm_task: DropFarmTask = Field(
        default=DropFarmTask.GLORY,
        title='世界之心刷怪点',
        description='世界之心靠击杀掉落：用哪个打怪任务的位置刷到掉为止'
                    '（为了荣耀 / 兽毛之争 / 黄金比例）')


class WorldTask(ConfigBase):
    """
    世界任务：每天一次，只在开放世界时段（09:00-24:00）里跑

    两个 NPC（世界之巅守门人 / 世界之巅任务榜）固定在域外灵岛，
    地图名写死在代码里（`ScriptTask.WORLD_TASK_MAP`），不做成配置。
    窗口是游戏固定的开门时间，不进配置（见 tasks/WorldTask/schedule.py）；
    注意 server_update / delay_date 对本任务不生效：排期由脚本按窗口自己算，
    写入时走 server=False（见 script_task.schedule_next_run）。
    """
    scheduler: Scheduler = Field(default_factory=Scheduler)
    world_task_config: WorldTaskConfig = Field(default_factory=WorldTaskConfig)
    # 打怪类任务（兽毛之争 / 黄金比例 / 为了荣耀）用的战斗预设
    battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)
