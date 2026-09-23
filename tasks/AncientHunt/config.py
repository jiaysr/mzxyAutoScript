# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class AncientHuntConfig(BaseModel):
    # 目标地点：上古狩猎神在牧野，任务先把角色传送到该地点的传送落点
    target_location: str = Field(default='牧野', description='上古狩猎的目标地点（传送到该地点的落点）')
    # 锁定时用于匹配的目标名称
    target_name: str = Field(default='上古狩猎神', description='锁定目标的名称')
    # 活动-活跃页里的任务名，用于判断今天是否已经参与过（参与一次就够）
    active_task_name: str = Field(default='上古狩猎', description='活动-活跃页里的任务名')
    # 开放时间段，格式 HH:MM-HH:MM，多个用英文逗号分隔
    open_times: str = Field(default='12:35-12:45,17:05-17:15',
                            description='开放时间段（HH:MM-HH:MM，多个用逗号分隔，默认 12:35-12:45,17:05-17:15）')
    # 提前准备时间：提前这么久把游戏准备到主页面
    advance_time: Time = Field(default=Time(minute=3), description='提前准备时间，提前这么久把游戏准备到主页面')
    # 锁定目标的超时时间
    lock_timeout: int = Field(default=60, description='锁定目标超时时间（秒）')
    # 点击「进入上古狩猎场」后等待的秒数，随后重启游戏
    enter_wait: int = Field(default=2, description='进入狩猎场后等待秒数（随后重启游戏）')


class AncientHunt(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    ancient_hunt_config: AncientHuntConfig = Field(default_factory=AncientHuntConfig)
