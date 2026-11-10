# This Python file uses the following encoding: utf-8
from pydantic import Field

from tasks.Component.config_scheduler import Scheduler


class MikuXianjingScheduler(Scheduler):
    """
    迷窟仙境调度器：无时段、持续刷怪的空闲挂机任务

    没有时间窗口，启用后一直打，直到进度 100% 收工；检测到更高优先级任务到期时
    离场让路，稍后重排自己。优先级略高于野外雪人（ConfigManual.SCHEDULER_PRIORITY
    里排在 WildSnowman 之前），给其它正式任务让路。
    """
    priority: int = Field(default=100, description='priority_help')