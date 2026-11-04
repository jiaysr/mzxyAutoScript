# This Python file uses the following encoding: utf-8
"""帝玉岭[多人]（内部 Boss/出口点位与单人共用同一套小地图坐标）"""
from tasks.Dungeon.dungeons.DiyulingBase import DiyulingBase


class DiyulingMulti(DiyulingBase):
    name = '帝玉岭[多人]'
    FIELD = 'diyuling_multi'
    MAP_NAME = '帝玉岭[多人]'
    ENTRY_CLICK_NAME = 'diyuling_multi_entry'
    ENTRY_POSITION = (112, 223)
    EXIT_POSITION = (223, 166)
    BOSSES = (
        ('沙王', 'diyuling_shawang', (115, 160)),
        ('幽灵', 'diyuling_youling', (199, 123)),
    )
