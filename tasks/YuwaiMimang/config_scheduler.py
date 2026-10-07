# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class YuwaiMimangScheduler(Scheduler):
    """
    域外迷窟调度器：优先级仅次于 Restart（游戏崩了要先重启，所以 Restart 仍排最前）

    priority = 1 的作用是 `schedule_rule = Priority` 模式下它排在所有日常任务之前；
    `schedule_rule = Filter`（默认）下真正的优先级由 ConfigManual.SCHEDULER_PRIORITY
    的书写顺序决定，本任务也放在那里 Restart 之后的第一位。

    勾选后：时段内本任务到期 -> 一直是 pending -> 调度器每轮都先跑它，
    其他任务只能在 waiting 队列里等着，直到本任务 set_next_run 排到下一个时段。
    """
    priority: int = Field(default=1, description='priority_help')