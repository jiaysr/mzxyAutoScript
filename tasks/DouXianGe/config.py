# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class DouXianGeConfig(BaseModel):
    challenge_count: int = Field(default=0, ge=0,
                                 description='挑战次数（0=把剩余次数全部打完，含特权额外挑战次数）')


class DouXianGe(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    douxian_config: DouXianGeConfig = Field(default_factory=DouXianGeConfig)
