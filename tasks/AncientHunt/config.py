# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class AncientHuntConfig(BaseModel):
    # 上古狩猎的目标地点：任务先把角色传送到该地点的传送落点上
    target_location: str = Field(default='牧野', description='上古狩猎的目标地点（传送到该地点的落点后开始狩猎）')


class AncientHunt(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    ancient_hunt_config: AncientHuntConfig = Field(default_factory=AncientHuntConfig)
