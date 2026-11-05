# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class WorldTask(ConfigBase):
    """
    世界任务：每天一次，只在开放世界时段（09:00-24:00）里跑

    窗口是游戏固定的开门时间，不进配置（见 tasks/WorldTask/schedule.py）；
    这里只有调度器：是否启用 / 下次运行 / 优先级 / 成功失败间隔。
    注意 server_update / delay_date 对本任务不生效：排期由脚本按窗口自己算，
    写入时走 server=False（见 script_task.schedule_next_run）。
    玩法配置（进图方式、具体做什么任务）等游戏流程确定后再往这里加。
    """
    scheduler: Scheduler = Field(default_factory=Scheduler)
