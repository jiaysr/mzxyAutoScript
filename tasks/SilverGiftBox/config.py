# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class SilverGiftBoxConfig(BaseModel):
    # NPC 名：小地图右侧 NPC 列表里要点的商人
    npc_name: str = Field(default='宝具商人', description='小地图 NPC 列表里要点击的 NPC 名')
    # NPC 所在地图：先传送到这里，再开小地图点 NPC
    relay_location: str = Field(default='牧野', description='NPC 所在地图（先传送到这里再开小地图）')
    # 点完 NPC 后等自动寻路走到的超时（秒）
    arrive_timeout: int = Field(default=120, description='点完 NPC 后等自动寻路走到的超时（秒）')
    # 右上角坐标稳定多少秒判定为已走到 NPC 处
    arrive_stable: int = Field(default=3, description='右上角坐标稳定多少秒判定为已走到 NPC 处')
    # NPC 对话框里的兑换选项名
    shop_option: str = Field(default='玲珑换宝', description='NPC 对话框里的兑换选项名')


class SilverGiftBox(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    silver_gift_box_config: SilverGiftBoxConfig = Field(default_factory=SilverGiftBoxConfig)
