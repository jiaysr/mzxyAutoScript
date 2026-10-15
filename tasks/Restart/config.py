# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import BaseModel, Field

from tasks.Restart.config_scheduler import RestartScheduler
from tasks.Component.config_base import ConfigBase, DateTime


class TasksReset(BaseModel):
    reset_task_datetime_enable: bool = Field(default=False, description='reset_task_datetime_enable_help')
    reset_task_datetime: DateTime = Field(default="2023-01-01 00:00:00", description='rest_task_datetime_help')


class LoginCharacterConfig(BaseModel):
    # 同账号多个角色时,需要登录的角色名
    character: str = Field(default="")


class LoginConfig(BaseModel):
    # 登录总时长上限（秒）：累计超过仍未进入游戏按业务失败处理
    # （任务自己推送通知 + 抛 TaskFailed，由 handle_error 决定是否继续）
    login_timeout: int = Field(default=240, ge=60, le=1800,
                               description='restart_login_timeout_help')


class Restart(ConfigBase):
    scheduler: RestartScheduler = Field(default_factory=RestartScheduler)
    tasks_config_reset: TasksReset = Field(default_factory=TasksReset)
    login_character_config: LoginCharacterConfig = Field(default_factory=LoginCharacterConfig)
    login_config: LoginConfig = Field(default_factory=LoginConfig)
