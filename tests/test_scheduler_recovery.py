# -*- coding: utf-8 -*-
"""
调度器「游戏没运行」恢复逻辑的单元测试（不连设备）

任务因为游戏没运行被中断时：
- 必须排 Restart 把游戏拉起来
- 同时要把本任务重新排上：很多任务的 finally 会无条件 set_next_run
  （AutoTask 会把自己排到第二天的同一时刻），只排 Restart 不重排任务，
  这个任务当天就丢了（实测 2026-10-23 00:20 自动任务）
- 连续多次仍不行就放弃，避免游戏反复闪退时死循环

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_scheduler_recovery.py
装了 pytest 时也可以：python -m pytest tests/test_scheduler_recovery.py
"""
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from script import Script


class FakeConfig:
    def __init__(self):
        self.calls = []

    def task_call(self, task, force_call=True):
        self.calls.append(task)
        return True


def make_script() -> Script:
    """只够恢复逻辑用的 Script（绕过 __init__，不连设备）"""
    obj = Script.__new__(Script)
    obj.config = FakeConfig()
    obj.game_not_running_retry = {}
    obj.game_not_running_aborted = False
    return obj


def test_restart_then_requeue_task():
    """中断后：先排 Restart，再把本任务重新排上"""
    script = make_script()
    script._handle_game_not_running('AutoTask')
    assert script.config.calls == ['Restart', 'AutoTask']
    assert script.game_not_running_aborted is True


def test_gives_up_after_retry_limit():
    """连续中断超过上限：只拉起游戏，不再重排任务"""
    script = make_script()
    for _ in range(Script.GAME_NOT_RUNNING_RETRY):
        script._handle_game_not_running('AutoTask')
    script.config.calls.clear()
    script._handle_game_not_running('AutoTask')
    assert script.config.calls == ['Restart'], '超过上限后不再重排本任务'
    assert script.game_not_running_retry['AutoTask'] == Script.GAME_NOT_RUNNING_RETRY + 1


def test_counters_are_per_task():
    """不同任务各自计数，互不影响"""
    script = make_script()
    script._handle_game_not_running('AutoTask')
    script._handle_game_not_running('Quiz')
    assert script.config.calls == ['Restart', 'AutoTask', 'Restart', 'Quiz']
    assert script.game_not_running_retry == {'AutoTask': 1, 'Quiz': 1}


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
