# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class YuwaiMimangScheduler(Scheduler):
    """
    域外迷窟调度器：排在 Restart 和世界首领之后

    上午 11:00 的世界首领只刷一次，错过就过期；迷窟同一时段内一直有怪，
    晚回去几分钟只是少打一会儿。所以两个任务同时到期时先让世界首领跑，
    本任务用共享默认优先级（5），而 `WorldBossScheduler.priority = 1`。

    `schedule_rule = Filter`（默认）下真正的顺序由
    ConfigManual.SCHEDULER_PRIORITY 的书写顺序决定，本任务在 WorldBoss 之后。

    勾选后：时段内本任务到期 -> 一直是 pending -> 调度器每轮都先跑它，
    其他任务只能在 waiting 队列里等着，直到本任务 set_next_run 排到下一个时段。
    """
    priority: int = Field(default=5, description='priority_help')
