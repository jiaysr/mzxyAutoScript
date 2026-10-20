# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import BaseModel, Field

from tasks.Restart.config_scheduler import RestartScheduler
from tasks.Component.config_base import ConfigBase, DateTime


class TasksReset(BaseModel):
    reset_task_datetime_enable: bool = Field(default=False, description='reset_task_datetime_enable_help')
    reset_task_datetime: DateTime = Field(default="2023-01-01 00:00:00", description='rest_task_datetime_help')


class LoginTargetConfig(BaseModel):
    # 区服：填数字区服（如 88区）；留空使用上次登录区服
    server: str = Field(default="", description='restart_login_server_help')
    # 小号账号名（如 霸刀）；留空使用上次登录账号
    account: str = Field(default="", description='restart_login_account_help')
    # 角色名（如 星沉沧海）；留空默认左侧角色
    character: str = Field(default="", description='restart_login_character_help')


class LoginX7Config(LoginTargetConfig):
    """小七服登录目标"""


class LoginOfficialConfig(LoginTargetConfig):
    """官方服登录目标与本地保存的登录凭据"""
    account: str = Field(default="", description='restart_login_official_account_help')
    password: str = Field(default="", repr=False, description='restart_login_password_help')


class LoginConfig(BaseModel):
    # 登录总时长上限（秒）：累计超过仍未进入游戏按业务失败处理
    # （任务自己推送通知 + 抛 TaskFailed，由 handle_error 决定是否继续）
    login_timeout: int = Field(default=240, ge=60, le=1800,
                               description='restart_login_timeout_help')


class Restart(ConfigBase):
    scheduler: RestartScheduler = Field(default_factory=RestartScheduler)
    tasks_config_reset: TasksReset = Field(default_factory=TasksReset)
    login_config: LoginConfig = Field(default_factory=LoginConfig)
    login_x7_config: LoginX7Config = Field(default_factory=LoginX7Config)
    login_official_config: LoginOfficialConfig = Field(default_factory=LoginOfficialConfig)
