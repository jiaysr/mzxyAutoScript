# This Python file uses the following encoding: utf-8
"""
伏魔山涧 配置（FumoShanjian）

当前进度：通过万剑冢的伏魔大使 NPC 打开对话，OCR 找到「进入伏魔涧」并点击，
再点对话框「确定」进入副本。
后续副本内的玩法参数再往这里补。
"""
from pydantic import ConfigDict, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class FumoShanjianConfig(ConfigBase):
    """伏魔山涧参数"""
    # script_set_arg 用 setattr 写配置，pydantic 默认不校验赋值，
    # 不开这项 ge/le 会被绕过
    model_config = ConfigDict(validate_assignment=True)

    npc_wait_timeout: int = Field(default=20, ge=5, le=120,
                                  description='点击小地图 NPC 后等待「伏魔大使」对话弹窗出现的超时（秒）')
    enter_option: str = Field(default='进入伏魔涧',
                              description='伏魔大使对话框里进入副本的选项文字（OCR 定位后点击）')
    enter_wait: int = Field(default=20, ge=5, le=120,
                            description='点「确定」后等待地点切换到副本的超时（秒）')


class FumoShanjian(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    fumo_shanjian_config: FumoShanjianConfig = Field(default_factory=FumoShanjianConfig)
