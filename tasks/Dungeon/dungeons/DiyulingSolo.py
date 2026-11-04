# This Python file uses the following encoding: utf-8
"""帝玉岭[单人]"""
from tasks.Dungeon.dungeons.DiyulingBase import DiyulingBase


class DiyulingSolo(DiyulingBase):
    name = '帝玉岭[单人]'
    FIELD = 'diyuling_solo'
    MAP_NAME = '帝玉岭[单人]'
    ENTRY_CLICK_NAME = 'diyuling_solo_entry'
    ENTRY_POSITION = (112, 223)
    EXIT_POSITION = (176, 166)
    BOSSES = (
        ('沙王', 'diyuling_solo_boss1', (115, 160)),
        ('幽灵', 'diyuling_solo_boss2', (199, 123)),
    )
