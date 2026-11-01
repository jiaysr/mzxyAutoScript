# -*- coding: utf-8 -*-
"""
自动任务-加量卡切换顺序的单元测试（不连设备）

游戏同一时间只允许一张加量卡生效：要激活另一张，必须先停掉当前生效的那张。
实测（2026-10-22 00:21 日志）：5 倍卡生效时直接点 3 倍卡的「激活加量卡」只会弹提示，
状态不翻转，连点 10 次直到 20s 超时 -> TaskFailed「加量卡3x切换失败」。

所以 set_bonus_state 的调用顺序有硬性要求：先停非目标侧，再激活目标侧。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_autotask_bonus.py
装了 pytest 时也可以：python -m pytest tests/test_autotask_bonus.py
"""
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.AutoTask.config import BonusMultiplier
from tasks.AutoTask.script_task import ScriptTask


def bonus_calls(want: BonusMultiplier) -> list:
    """只跑 set_bonus_state（用假 self 记录 set_bonus_side 的调用顺序）"""
    calls = []

    def fake_set_bonus_side(is_left, active, timeout=20):
        calls.append((is_left, active))

    ScriptTask.set_bonus_state(SimpleNamespace(set_bonus_side=fake_set_bonus_side), want)
    return calls


def test_want_3x_stops_5x_first():
    """要 3 倍：先停 5 倍（右），再激活 3 倍（左）"""
    assert bonus_calls(BonusMultiplier.X3) == [(False, False), (True, True)]


def test_want_5x_stops_3x_first():
    """要 5 倍：先停 3 倍（左），再激活 5 倍（右）"""
    assert bonus_calls(BonusMultiplier.X5) == [(True, False), (False, True)]


def test_want_none_stops_both():
    """不要加量：两边都停（顺序无所谓）"""
    assert sorted(bonus_calls(BonusMultiplier.NONE)) == [(False, False), (True, False)]


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
