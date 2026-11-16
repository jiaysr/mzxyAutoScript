# -*- coding: utf-8 -*-
"""副本失败容忍回归测试（不连设备）

真机 2026-11-16 两次事故：
- 02:01 沙狱[多人]：传送去牧野的途中被玩家打死，复活在域外灵岛，
  `map_teleport` 还在等「到牧野」-> 干等 60s 超时 -> run_once 返回 False
  -> runner 抛 `RuntimeError: Run failed for dungeon [沙狱[多人]]`；
- 02:54 仙居福地（特权局）：鑫打了 180s 没打死 -> `Fight timeout` -> run_once 返回 False
  -> `RuntimeError: Privilege run failed`。

两次都把整个「副本玩法」打死，当天剩下的副本（含特权次数）全部白丢。这里锁住：
1. 副本**外**阵亡 = 重试当前这一局（次数没消耗），连续几次站不住脚才跳过该副本；
2. 单个副本失败/重置失败只跳过它自己，其它副本照跑，不再抛 RuntimeError。
"""
from types import SimpleNamespace

import pytest

from tasks.Dungeon.common import DungeonBase, DungeonRetry
from tasks.Dungeon.runner import DungeonAborted, DungeonRunner
from tasks.Dungeon.script_task import ScriptTask


# ---------------------------------------------------------------- runner：单局失败
def test_run_once_failure_skips_that_dungeon_and_continues():
    calls = []

    def run_once(field):
        calls.append(field)
        return field != 'a'          # a 没打成，b 正常

    counts = {'a': 1, 'b': 1}
    runner = DungeonRunner(
        targets=[{'field': 'a', 'name': 'A', 'configured': 1},
                 {'field': 'b', 'name': 'B', 'configured': 1}],
        privilege_field=None,
        status=lambda: {'counts': dict(counts), 'privilege': 1},
        select=lambda name: True,
        reset=lambda: True,
        run_once=run_once,
        field_status=lambda field: (counts[field] - 1, 1),
    )

    runner.run()                     # 不抛 RuntimeError

    assert calls == ['a', 'b']       # 第一个失败后仍然跑第二个
    assert 'a' in runner._aborted
    assert 'b' not in runner._aborted


def test_privilege_run_failure_skips_that_dungeon():
    calls = []

    def run_once(field):
        calls.append(field)
        return False

    runner = DungeonRunner(
        targets=[{'field': 'x', 'name': 'X', 'configured': 2}],
        privilege_field='x',
        status=lambda: {'counts': {'x': 0}, 'privilege': 1},
        select=lambda name: True,
        reset=lambda: True,
        run_once=run_once,
        field_status=lambda field: (0, 1),
    )

    runner.run()                     # 特权局失败也不抛

    assert calls == ['x']
    assert 'x' in runner._aborted


def test_reset_failure_only_skips_that_dungeon():
    calls = []

    def run_once(field):
        calls.append(field)
        return True

    def reset():
        raise RuntimeError('Reset did not decrease [A] count (2 -> 2)')

    counts = {'a': 2, 'b': 1}
    runner = DungeonRunner(
        targets=[{'field': 'a', 'name': 'A', 'configured': 2},
                 {'field': 'b', 'name': 'B', 'configured': 1}],
        privilege_field=None,
        status=lambda: {'counts': dict(counts), 'privilege': 1},
        select=lambda name: True,
        reset=reset,
        run_once=run_once,
        field_status=lambda field: (counts[field], 1),   # 次数不自动减 -> 需要重置
    )

    runner.run()

    assert calls == ['a', 'b']
    assert 'a' in runner._aborted


# ---------------------------------------------------------------- 副本外阵亡重试
class FakeDungeonScript:
    """只测 dungeon_run_once 的重试逻辑"""

    DEATH_RETRY = 2

    def __init__(self, results):
        self.results = list(results)
        self.instances = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None)
        self.config = SimpleNamespace(dungeon=SimpleNamespace(
            dungeon_config=SimpleNamespace(dungeon_interval=0)))

    def enabled_dungeons(self, cfg):
        outer = self

        class FakeDungeon:
            name = '假副本'

            def __init__(self, config, device):
                outer.instances += 1

            def run_once(self):
                result = outer.results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

        return [('fake', FakeDungeon)]


def test_dungeon_run_once_retries_after_death_outside():
    fake = FakeDungeonScript([DungeonRetry('假副本'), True])
    assert ScriptTask.dungeon_run_once(fake, 'fake') is True
    assert fake.instances == 2       # 第一次阵亡，第二次重试成功


def test_dungeon_run_once_gives_up_after_death_retry_limit():
    fake = FakeDungeonScript([DungeonRetry('假副本')] * 3)
    with pytest.raises(DungeonAborted):
        ScriptTask.dungeon_run_once(fake, 'fake')
    assert fake.instances == FakeDungeonScript.DEATH_RETRY


def test_handle_death_resume_raises_retry():
    class FakeDungeon:
        name = '假副本'
        device = SimpleNamespace(sleep=lambda seconds: None)

    with pytest.raises(DungeonRetry):
        DungeonBase.handle_death_resume(FakeDungeon())


# ---------------------------------------------------------------- 仙居福地：两种死亡要分开
def test_xianju_fudi_death_inside_counts_as_completed():
    """副本内阵亡（已进图）：不抛重试异常，由地点判定按「完成一次」收尾"""
    from tasks.Dungeon.dungeons.XianjuFudi import XianjuFudi

    class Fake(XianjuFudi):
        def __init__(self):
            self.device = SimpleNamespace(sleep=lambda seconds: None)
            self._entered_dungeon = True

    XianjuFudi.handle_death_resume(Fake())        # 不抛异常


def test_xianju_fudi_death_outside_retries():
    """还没进图就阵亡（骨牢治疗/传送途中）：抛 DungeonRetry 重试这一局"""
    from tasks.Dungeon.dungeons.XianjuFudi import XianjuFudi

    class Fake(XianjuFudi):
        def __init__(self):
            self.device = SimpleNamespace(sleep=lambda seconds: None)
            self._entered_dungeon = False

    with pytest.raises(DungeonRetry):
        XianjuFudi.handle_death_resume(Fake())
