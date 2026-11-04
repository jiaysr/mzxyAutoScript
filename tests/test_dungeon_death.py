# -*- coding: utf-8 -*-
"""副本死亡处理单测（不连设备）"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.runner import DungeonAborted


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def push(self, **kwargs):
        self.calls.append(kwargs)
        return True


def make_task(location):
    task = DungeonBase.__new__(DungeonBase)
    task.name = '沙狱[单人]'
    task.config = SimpleNamespace(config_name='oas1', notifier=FakeNotifier())
    task.map_current_location = lambda: location
    return task


def test_left_dungeon_map_notifies_and_aborts():
    task = make_task(('沙狱外', 128, 182))
    try:
        task.dungeon_check_in_map('沙狱[单人]')
    except DungeonAborted as e:
        assert str(e) == '沙狱[单人]未通关'
    else:
        raise AssertionError('DungeonAborted not raised')
    call = task.config.notifier.calls[0]
    assert call['title'] == '沙狱[单人]未通关'
    assert '未通关' in call['content']


def test_still_in_dungeon_map_does_not_abort():
    task = make_task(('沙狱[单人]', 100, 100))
    task.dungeon_check_in_map('沙狱[单人]')
    assert task.config.notifier.calls == []


def test_death_outside_dungeon_revives_without_rerun():
    task = make_task(None)
    calls = []
    task.appear = lambda rule: True
    task.revive = lambda: calls.append('revive')
    task.rerun_task = lambda: calls.append('rerun')
    task.handle_death()
    assert calls == ['revive']


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'---- {len(tests)}/{len(tests)} passed ----')
