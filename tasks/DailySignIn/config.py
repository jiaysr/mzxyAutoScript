# This Python file uses the following encoding: utf-8
"""
每日签到 配置（DailySignIn）

任务框架阶段：先把配置项和任务入口接好，具体页面流程后续按素材补。
"""
from pydantic import ConfigDict, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class DailySignInConfig(ConfigBase):
    """每日签到参数"""
    # script_set_arg 用 setattr 写配置，pydantic 默认不校验赋值
    model_config = ConfigDict(validate_assignment=True)

    # 是否修心
    xiuxin: bool = Field(default=True, description='daily_sign_in_xiuxin_help')
    # 是否领取 VIP 每日礼包
    claim_vip_gift: bool = Field(default=True, description='daily_sign_in_claim_vip_gift_help')
    # 是否领取俸禄
    claim_salary: bool = Field(default=True, description='daily_sign_in_claim_salary_help')
    # 是否领取生肖福利礼包
    claim_zodiac_gift: bool = Field(default=True,
                                    description='daily_sign_in_claim_zodiac_gift_help')


class DailySignIn(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    daily_sign_in_config: DailySignInConfig = Field(default_factory=DailySignInConfig)
