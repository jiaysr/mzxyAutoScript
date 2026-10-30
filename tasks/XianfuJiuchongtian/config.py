# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.battle_config import BattleTaskConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class XianfuConfig(BaseModel):
    stage: int = Field(default=1, ge=1, le=4,
                       description='阶段：打各大关的第几小关（1南极/2北极/3东极/4西极）')
    stone_threshold: int = Field(default=40, ge=4, le=40,
                                 description='仙石达到该值才开始清仙石（4-40，每半小时恢复1点）')
    max_battles: int = Field(default=0, ge=0,
                             description='单次最多打几局（0=不限，一直打到仙石不够）')
    fixed_daguan: int = Field(default=0, ge=0, le=4,
                              description='指定大关：1增长天/2多闻天/3持国天/4广目天；'
                                          '0=按背包碎片数量自动选（指定后跳过背包统计）')
    target_coord: str = Field(default='210,158', description='副本内怪物坐标 x,y')


class XianfuJiuchongtian(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    xianfu_config: XianfuConfig = Field(default_factory=XianfuConfig)
    battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)
