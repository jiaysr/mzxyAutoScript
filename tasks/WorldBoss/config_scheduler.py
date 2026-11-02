# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_scheduler import Scheduler


class WorldBossScheduler(Scheduler):
    """
    世界首领调度器：排在域外迷窟之前（两者原来是对调的）

    理由：上午 11:00 的首领只刷一次，错过就过期；而迷窟同一时段内一直有怪，
    晚回去几分钟只是少打一会儿。所以 11:00 两个任务同时到期时必须先让首领跑。

    priority = 1 的作用是 `schedule_rule = Priority` 模式下它排在所有日常任务之前、
    Restart（0）之后；`schedule_rule = Filter`（默认）下真正的顺序由
    ConfigManual.SCHEDULER_PRIORITY 的书写顺序决定，本任务也放在迷窟之前。
    """
    priority: int = Field(default=1, description='priority_help')
