# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class WorldTaskConfig(BaseModel):
    # NPC 所在地图：脚本先判断在这里，不在就传送过来，然后开小地图点 NPC
    location: str = Field(default='域外灵岛',
                          description='NPC 所在地图（不在该地图会先传送过去）')
    # 接任务时是否使用加量；不加量时点「接取」弹出的加量推广弹窗会自动勾「今日不再提示」并关掉
    use_bonus: bool = Field(default=False, title='使用加量',
                            description='接取跨服任务时使用任务加量（复用自动任务的加量卡逻辑）')


class WorldTask(ConfigBase):
    """
    世界任务：每天一次，只在开放世界时段（09:00-24:00）里跑

    窗口是游戏固定的开门时间，不进配置（见 tasks/WorldTask/schedule.py）；
    注意 server_update / delay_date 对本任务不生效：排期由脚本按窗口自己算，
    写入时走 server=False（见 script_task.schedule_next_run）。
    """
    scheduler: Scheduler = Field(default_factory=Scheduler)
    world_task_config: WorldTaskConfig = Field(default_factory=WorldTaskConfig)
