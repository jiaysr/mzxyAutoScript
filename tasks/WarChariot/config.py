# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class WarChariotConfig(BaseModel):
    start_time: Time = Field(default=Time(hour=20, minute=0, second=0), description="仙盟战车每天开放时间，默认 20:00")
    advance_time: Time = Field(default=Time(minute=5), description="提前准备时间，提前这么久把游戏启动/登录到主页面")
    keyword: str = Field(default="仙盟战车", description="检测区域里要匹配的文字")
    check_interval: int = Field(default=2, description="检测间隔（秒）")
    check_timeout: int = Field(default=1200, description="检测超时时间（秒），超时后同样标记任务完成")
    offline_time: Time = Field(default=Time(minute=5), description="参与成功后关闭游戏的时长")


class WarChariot(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    war_chariot_config: WarChariotConfig = Field(default_factory=WarChariotConfig)
