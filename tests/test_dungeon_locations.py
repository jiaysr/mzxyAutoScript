# -*- coding: utf-8 -*-
"""副本地图入口配置单测（不连设备）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.dungeons.DiyulingMulti import DiyulingMulti
from tasks.Dungeon.dungeons.DiyulingSolo import DiyulingSolo
from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing


def test_outer_entries():
    entries = DungeonBase.DUNGEON_OUTER_ENTRIES
    assert entries['shayu_multi']['outer'] == '沙狱外'
    assert entries['shayu_solo']['outer'] == '沙狱外'
    assert entries['diyuling_multi']['outer'] == '帝玉岭外'
    assert entries['diyuling_solo']['outer'] == '帝玉岭外'
    assert entries['shayu_multi']['entry'] == 'shayu_outer_entry'
    assert entries['diyuling_multi']['entry'] == 'diyuling_outer_entry'


def test_unrecorded_outer_entries():
    entries = DungeonBase.DUNGEON_OUTER_ENTRIES
    assert entries['liuli_huanjing']['base'] == '忘川'
    assert entries['liuli_huanjing']['entry'] is None
    assert 'xianju_fudi' not in entries
    assert 'tianyuan_lingdao' not in entries


def test_diyuling_locations():
    assert DiyulingSolo.MAP_NAME == '帝玉岭[单人]'
    assert DiyulingSolo.ENTRY_POSITION == (112, 223)
    assert DiyulingSolo.EXIT_POSITION == (176, 166)
    assert DiyulingMulti.MAP_NAME == '帝玉岭[多人]'
    assert DiyulingMulti.EXIT_POSITION == (223, 166)
    assert [boss[0] for boss in DiyulingSolo.BOSSES] == ['沙王', '幽灵']


def test_liuli_locations():
    assert LiuliHuanjing.MAP_NAME == '琉璃幻境[副本]'
    assert LiuliHuanjing.ENTRY_POSITION == (27, 264)
    assert LiuliHuanjing.EXIT_MAP == '忘川'
    assert LiuliHuanjing.EXIT_POSITION == (311, 453)
    assert [boss[0] for boss in LiuliHuanjing.BOSSES] == [
        '阴阳脸', '逍遥愁', '梦魇兽', '守墓人']


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'---- {len(tests)}/{len(tests)} passed ----')
