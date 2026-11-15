# -*- coding: utf-8 -*-
"""副本前准备顺序回归测试（不连设备）

2026-11-15 调整：所有副本统一成「进外地图 -> 修理装备 -> 万事通治疗」（先修后治）。
原来沙狱/帝玉岭/琉璃/仙居福地各自写一遍 heal -> repair，改顺序得改四处、容易漏，
现在统一走 `DungeonBase.dungeon_prepare_run()`，这里锁住顺序和调用入口。
"""
import pytest

from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.dungeons.DiyulingMulti import DiyulingMulti
from tasks.Dungeon.dungeons.DiyulingSolo import DiyulingSolo
from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing
from tasks.Dungeon.dungeons.ShayuMulti import ShayuMulti
from tasks.Dungeon.dungeons.ShayuSolo import ShayuSolo
from tasks.Dungeon.dungeons.XianjuFudi import XianjuFudi

DUNGEONS = (ShayuSolo, ShayuMulti, DiyulingSolo, DiyulingMulti,
            LiuliHuanjing, XianjuFudi)


class FakePrepare(DungeonBase):
    """只测 dungeon_prepare_run 的顺序"""

    def __init__(self, outer_ok=True, repair_ok=True, heal_ok=True):
        self.calls = []
        self.outer_ok = outer_ok
        self.repair_ok = repair_ok
        self.heal_ok = heal_ok

    def dungeon_enter_outer(self, field):
        self.calls.append(('enter_outer', field))
        return self.outer_ok

    def dungeon_repair_equipment(self):
        self.calls.append('repair')
        return self.repair_ok

    def dungeon_heal(self):
        self.calls.append('heal')
        return self.heal_ok


def test_prepare_run_repairs_before_healing():
    fake = FakePrepare()
    assert DungeonBase.dungeon_prepare_run(fake, 'shayu_solo') is True
    assert fake.calls == [('enter_outer', 'shayu_solo'), 'repair', 'heal']


def test_prepare_run_stops_when_outer_map_fails():
    fake = FakePrepare(outer_ok=False)
    assert DungeonBase.dungeon_prepare_run(fake, 'shayu_solo') is False
    # 外地图都进不去就别修/治了
    assert fake.calls == [('enter_outer', 'shayu_solo')]


def test_prepare_run_continues_when_repair_or_heal_fails():
    fake = FakePrepare(repair_ok=False, heal_ok=False)
    assert DungeonBase.dungeon_prepare_run(fake, 'xianju_fudi') is True
    # 修/治失败只记日志，不该吃掉这一局的次数
    assert fake.calls == [('enter_outer', 'xianju_fudi'), 'repair', 'heal']


@pytest.mark.parametrize('dungeon_cls', DUNGEONS)
def test_every_dungeon_prepares_through_shared_helper(dungeon_cls):
    calls = []

    class Fake(dungeon_cls):
        def __init__(self):          # 不调父类（会连设备/OCR）
            self.calls = calls

        def dungeon_prepare_run(self, field):
            self.calls.append(('prepare', field))
            return False             # 停在准备阶段

    fake = Fake()
    assert dungeon_cls.run_once(fake) is False
    assert calls == [('prepare', dungeon_cls.FIELD)]
