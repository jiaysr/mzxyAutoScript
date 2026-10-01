# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class CrossArenaConfig(BaseModel):
    use_activity: bool = Field(default=False, description="根据活跃度判断（勾选后每日次数失效，活跃任务完成即收工）")
    count: int = Field(default=10, description="每日完成次数（仅未勾选活跃度时生效）")
    match_timeout: int = Field(default=1200, description="等待匹配成功的超时时间（秒）")
    completed: int = Field(default=0, description="今日已完成次数（自动维护）")


class CrossArena(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    cross_arena_config: CrossArenaConfig = Field(default_factory=CrossArenaConfig)
