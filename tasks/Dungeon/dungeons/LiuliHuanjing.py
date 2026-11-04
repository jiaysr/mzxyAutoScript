# This Python file uses the following encoding: utf-8
"""
琉璃幻境

入口在忘川（无独立外地图）：忘川万事通 / 副本入口 / 副本内全部小地图操作都在视口左下角。
副本名：琉璃幻境[副本]，初始 27,264，四个 Boss，出口回忘川 311,453。
"""
from tasks.Dungeon.dungeons.DiyulingBase import DiyulingBase


class LiuliHuanjing(DiyulingBase):
    name = '琉璃幻境'
    FIELD = 'liuli_huanjing'
    MAP_NAME = '琉璃幻境[副本]'
    ENTRY_CLICK_NAME = 'liuli_entry'
    ENTRY_POSITION = (27, 264)
    EXIT_MAP = '忘川'
    EXIT_CLICK_NAME = 'liuli_exit'
    EXIT_POSITION = (311, 453)
    BOSSES = (
        ('阴阳脸', 'liuli_boss1', (118, 101)),
        ('逍遥愁', 'liuli_boss2', (131, 280)),
        ('梦魇兽', 'liuli_boss3', (276, 211)),
        ('守墓人', 'liuli_boss4', (236, 67)),
    )
