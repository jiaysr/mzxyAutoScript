# This Python file uses the following encoding: utf-8
"""
种植 配置（ZhongZhi）

任务框架阶段：先接入任务入口，具体种植/收获流程后续按素材补。
"""
from pydantic import Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class ZhongZhi(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
