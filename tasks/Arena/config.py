# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class ArenaConfig(BaseModel):
    count: int = Field(default=10, description="每日完成次数")
    match_timeout: int = Field(default=1200, description="等待匹配成功的超时时间（秒）")
    completed: int = Field(default=0, description="今日已完成次数（自动维护）")


class Arena(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    arena_config: ArenaConfig = Field(default_factory=ArenaConfig)
