# This Python file uses the following encoding: utf-8
"""
世界任务排期：开放世界每天 09:00 开门、24:00 关门

游戏里的开放世界不是全天开放的，所以这个任务的排期必须落在窗口内：

- 正常结束：下次 = 现在 + 成功/失败间隔（默认各 1 天），再夹进窗口；
- 被提前叫醒（手动运行、别的任务 task_call）而窗口没开：直接排到最近的一次 09:00，
  不做任何游戏内操作。

这里只放纯函数，不依赖设备/配置，方便直接跑单测（tests/test_world_task_schedule.py）。
窗口是游戏固定的开门时间，将来真要改成可配置，改 OPEN_HOUR / CLOSE_HOUR 即可。
"""
import random
from datetime import datetime, timedelta

# 开放世界开放时段：[09:00, 24:00)
OPEN_HOUR = 9
CLOSE_HOUR = 24

WINDOW_START_MINUTE = OPEN_HOUR * 60
WINDOW_END_MINUTE = CLOSE_HOUR * 60
# 窗口长度（秒），浮动时间不能把排期甩出窗口
WINDOW_SECONDS = (WINDOW_END_MINUTE - WINDOW_START_MINUTE) * 60


def minute_of_day(moment: datetime) -> int:
    """当天的第几分钟（00:00 -> 0）"""
    return moment.hour * 60 + moment.minute


def in_open_world(moment: datetime) -> bool:
    """这一刻开放世界开没开门"""
    return WINDOW_START_MINUTE <= minute_of_day(moment) < WINDOW_END_MINUTE


def open_world_start(moment: datetime, next_day: bool = False) -> datetime:
    """moment 当天（或次日）的开门时刻 09:00:00"""
    day = moment + timedelta(days=1) if next_day else moment
    return day.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)


def clamp_to_open_world(moment: datetime, float_seconds: int = 0) -> datetime:
    """
    把时间夹进开放世界时段：不在窗口内就顺延到最近的一次开门（09:00）

    10:00 -> 10:00（窗口内，不变）
    07:00 -> 当天 09:00
    23:59 -> 23:59（窗口内，不变）

    :param float_seconds: 被夹到 09:00 整时随机后移的秒数（对应排期的浮动时间），
                          0 表示不浮动。只在「夹到开门时刻」时才浮动，避免所有任务同一秒启动。
    """
    if in_open_world(moment):
        return moment
    # 关门之后再醒来（CLOSE_HOUR 改成 24 以外的值时才会发生）要顺延到次日
    after_close = minute_of_day(moment) >= WINDOW_END_MINUTE
    clamped = open_world_start(moment, next_day=after_close)
    if float_seconds > 0:
        clamped += timedelta(seconds=random.randint(0, min(int(float_seconds), WINDOW_SECONDS - 1)))
    return clamped


def next_run_after(start: datetime, interval: timedelta, float_seconds: int = 0) -> datetime:
    """start + interval，再夹进开放世界时段（间隔取 1 天即「每天一次」）"""
    return clamp_to_open_world(start + interval, float_seconds=float_seconds)
