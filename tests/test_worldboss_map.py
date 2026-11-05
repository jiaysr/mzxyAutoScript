# -*- coding: utf-8 -*-
"""
世界首领寻路前地图判断单测（不连设备）

    toolkit\\python.exe tests\\test_worldboss_map.py
"""
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.config.config import Config
from tasks.WorldBoss.script_task import ScriptTask


def make_task() -> ScriptTask:
    device = SimpleNamespace(image=None, sleep=lambda seconds: None)
    return ScriptTask(Config('oas1'), device)


def stub(task, location, teleport_ok=True, main_ok=True):
    calls = []
    task.map_move_ensure_main_page = lambda timeout=30: main_ok
    task.map_current_location = lambda: location
    task.map_name_match = lambda text, name: text == name
    task.map_teleport = lambda name: calls.append(name) or teleport_ok
    return calls


def test_already_on_boss_map():
    task = make_task()
    calls = stub(task, ('牧野', 10, 20))
    assert task.prepare_boss_map({'target_map': '牧野'}) is True
    assert calls == []


def test_teleport_when_on_other_map():
    task = make_task()
    calls = stub(task, ('沼泽', 10, 20))
    assert task.prepare_boss_map({'target_map': '牧野'}) is True
    assert calls == ['牧野']


def test_skip_teleport_when_location_unreadable():
    """
    地点读不出来：**不传送**、返回 False 交给调用方回退

    传送/列表页是覆盖式界面，会把左下角聊天栏挡掉，boss 结束消息就再也认不到了，
    所以读不到地点时宁可退回原寻路流程，也不平白触发一次传送。
    """
    task = make_task()
    calls = stub(task, None)
    assert task.prepare_boss_map({'target_map': '牧野'}) is False
    assert calls == []


def test_fallback_when_teleport_fails():
    task = make_task()
    calls = stub(task, ('沼泽', 10, 20), teleport_ok=False)
    assert task.prepare_boss_map({'target_map': '牧野'}) is False
    assert calls == ['牧野']


def test_fallback_when_main_page_not_reached():
    task = make_task()
    calls = stub(task, ('沼泽', 10, 20), main_ok=False)
    assert task.prepare_boss_map({'target_map': '牧野'}) is False
    assert calls == []


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
