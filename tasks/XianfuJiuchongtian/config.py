# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class XianfuConfig(BaseModel):
    stage: int = Field(default=1, ge=1, le=4,
                       description='阶段：打各大关的第几小关（1南极/2北极/3东极/4西极）')
    stone_threshold: int = Field(default=40, ge=4, le=40,
                                 description='仙石达到该值才开始清仙石（4-40，每半小时恢复1点）')
    max_battles: int = Field(default=0, ge=0,
                             description='单次最多打几局（0=不限，一直打到仙石不够）')
    click_empty_skills: bool = Field(default=False,
                                     description='是否也点空技能位（不同设备技能不同：有空位时不要点）')
    target_coord: str = Field(default='210,158', description='副本内怪物坐标 x,y')


class XianfuJiuchongtian(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    xianfu_config: XianfuConfig = Field(default_factory=XianfuConfig)
