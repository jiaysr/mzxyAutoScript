# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class Challenge(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
