# -*- coding: utf-8 -*-
"""
世界首领事件窗口单测：整点后 10 分钟之外不再执行

任务开始时间已经超过窗口（如 7:00 的 boss，7:11 才启动）时，
current_events() 不返回该事件，run() 会跳过并排期下一次。

    toolkit\\python.exe tests\\test_worldboss_window.py
"""
import sys
import traceback
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.config.config import Config
from tasks.WorldBoss.script_task import ScriptTask

EVENT_TIME = time(7, 0)


def make_task() -> ScriptTask:
    task = ScriptTask(Config('oas1'), SimpleNamespace(image=None))
    boss = {'key': 'manchui', 'name': '蛮锤'}
    task.iter_enabled_events = lambda: [(boss, EVENT_TIME)]
    return task


def at(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second)


def test_event_active_inside_window():
    task = make_task()
    events = task.current_events(at(31, 7, 9))
    assert len(events) == 1
    assert events[0][0]['key'] == 'manchui'
    assert events[0][1] == at(31, 7, 0)


def test_event_expired_at_window_end():
    task = make_task()
    assert task.current_events(at(31, 7, 10)) == []
    assert task.current_events(at(31, 7, 11)) == []


def test_event_not_started_yet():
    task = make_task()
    # 早于「出发时间」不返回（出发时间 = 整点 - advance_time）
    before_start = at(31, 7, 0) - task.advance - timedelta(minutes=1)
    assert task.current_events(before_start) == []


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
