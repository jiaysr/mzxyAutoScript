# -*- coding: utf-8 -*-
"""
不周山战斗收尾逻辑单测（不连设备）

覆盖两件在一次真实运行里很容易互相抵消的事：

1. `battle_relock_after_kill`（补锁）什么时候该做、什么时候该放弃
   —— 2026-11-05 日志实测：完整重锁 ~1.1s，补锁一次 ~0.45s，
   但每层最后一只是没有怪可补的，空切 3 次 ≈ 1.4s/层 × 10 层，比省下的还多。
   所以必须先用最新一帧挡掉「已出结算弹窗」「本层已清完」两种情况，并且只补 1 次。

2. `battle_interrupted`（中断）
   —— 结算弹窗压暗画面时，右上角菜单特征匹配不上，锁目标会白等满 6s。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_buzhoushan_battle.py
装了 pytest 时也可以：python -m pytest tests/test_buzhoushan_battle.py
"""
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Buzhoushan.script_task import ScriptTask


class FakeRule:
    """替身素材规则：只关心命中与否 + 被查了几次"""

    def __init__(self, hit: bool):
        self.hit = hit
        self.calls = 0

    def match(self, image, threshold=None):
        self.calls += 1
        return self.hit


class FakeDevice:
    image = object()


def make_task(settle: bool = False, teleport: bool = False,
              fast_relock: bool = True, relock_result: bool = True):
    """
    造一个只带「收尾判断」需要的那几个部件的任务实例

    :return: (task, relock_calls, task 上的两个替身素材)
    """
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.exist_image = lambda: True
    task.battle_fast_relock = lambda: fast_relock
    task.I_BUZHOUSHAN_SETTLE = FakeRule(settle)
    task.I_BUZHOUSHAN_TELEPORT = FakeRule(teleport)

    relock_calls = []

    def _relock(max_switch=None):
        relock_calls.append(max_switch)
        return relock_result

    task.battle_relock_target = _relock
    return task, relock_calls


def test_relock_when_target_may_still_be_there():
    """正常情况：补一次锁（max_switch=1），补到就一直打下去"""
    task, relock_calls = make_task(relock_result=True)
    assert task.battle_relock_after_kill() is True
    assert relock_calls == [1], f'补锁应只试 1 次，实际 {relock_calls}'


def test_no_relock_when_settlement_popup_shows():
    """已经出结算弹窗：交给循环收尾，别在弹窗上补锁"""
    task, relock_calls = make_task(settle=True)
    assert task.battle_relock_after_kill() is False
    assert relock_calls == [], f'结算弹窗上不该补锁: {relock_calls}'
    assert task.I_BUZHOUSHAN_SETTLE.calls == 1, '应该只看一次最新一帧'


def test_no_relock_when_floor_cleared():
    """本层已清完（小地图出传送点）：场上没怪，补锁只会空切"""
    task, relock_calls = make_task(teleport=True)
    assert task.battle_relock_after_kill() is False
    assert relock_calls == [], f'本层清完不该补锁: {relock_calls}'


def test_no_relock_when_disabled_in_config():
    """配置里关掉 fast_relock：完全不看不补（别偷偷多花一次素材匹配）"""
    task, relock_calls = make_task(fast_relock=False)
    assert task.battle_relock_after_kill() is False
    assert relock_calls == [], relock_calls
    assert task.I_BUZHOUSHAN_SETTLE.calls == 0, '关掉补锁时不该再查弹窗素材'


def test_relock_gives_up_without_target():
    """补锁没补到就返回 False，交回外层走完整流程"""
    task, relock_calls = make_task(relock_result=False)
    assert task.battle_relock_after_kill() is False
    assert relock_calls == [1], relock_calls


def test_battle_interrupted_by_settlement():
    """结算弹窗出现 = 战斗被打断，锁目标要立刻收工"""
    task, _ = make_task(settle=True)
    assert task.battle_interrupted() is True
    task, _ = make_task(settle=False)
    assert task.battle_interrupted() is False


def test_battle_interrupted_without_image():
    """一次都没截过图时不能炸，也不能误判成中断"""
    task, _ = make_task()
    task.exist_image = lambda: False
    assert task.battle_interrupted() is False


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
