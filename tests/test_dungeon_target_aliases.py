# -*- coding: utf-8 -*-
"""副本目标别名与 Boss 到达容差单测（不连设备）"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing
from tasks.Dungeon.runner import DungeonAborted
from tasks.GameUi.core.ocr import OcrHelpers


def test_liuli_alias_expansion():
    task = LiuliHuanjing.__new__(LiuliHuanjing)
    assert task._dungeon_target_names('梦魇兽') == ['梦魇兽', '梦魔兽']
    assert task._dungeon_target_names('守墓人') == ['守墓人']


def test_default_has_no_alias():
    task = DungeonBase.__new__(DungeonBase)
    assert task._dungeon_target_names('沙王') == ['沙王']


def test_alias_matches_ocr_variants():
    assert OcrHelpers.ocr_name_match('梦魔兽。', '梦魔兽')
    assert OcrHelpers.ocr_name_match('梦魔兽人。', '梦魔兽')
    assert not OcrHelpers.ocr_name_match('梦魔兽', '梦魇兽')


def make_walk_task(location):
    task = DungeonBase.__new__(DungeonBase)
    task.map_current_location = lambda: location
    task.map_name_match = lambda ocr_name, name: ocr_name == name
    task.device = SimpleNamespace(stuck_record_clear=lambda: None,
                                  sleep=lambda seconds: None)
    return task


def test_boss_tolerance_accepts_nearby_arrival():
    task = make_walk_task(('琉璃幻境[副本]', 247, 77))
    assert task.dungeon_wait_location('琉璃幻境[副本]', (236, 67),
                                      tolerance=20, timeout=1) is True
    assert task.dungeon_wait_location('琉璃幻境[副本]', (236, 67),
                                      tolerance=5, timeout=1) is False


def test_exit_only_checks_map_when_position_missing():
    task = make_walk_task(('沙狱外', 100, 100))
    assert task.dungeon_minimap_walk('top_left', object(), '沙狱外') is True


def make_boss_walk_task(location):
    task = DungeonBase.__new__(DungeonBase)
    task.device = SimpleNamespace(stuck_record_clear=lambda: None,
                                  sleep=lambda seconds: None)
    task.map_current_location = lambda: location
    task.map_name_match = lambda ocr_name, name: ocr_name == name
    task.dungeon_check_in_map = lambda map_name: None
    task.ui_close_menu = lambda: None
    task.screenshot = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
    task.dungeon_finish_visible = lambda image=None: False
    task.dungeon_boss_reward_visible = lambda image=None: False
    return task


def test_walk_to_boss_returns_when_target_locked():
    task = make_boss_walk_task(('琉璃幻境[副本]', 274, 211))
    calls = []
    task.dungeon_click_minimap_target = lambda corner, rule: calls.append('click') or True
    task.dungeon_try_lock_target = lambda targets: calls.append('lock') or True
    assert task.dungeon_walk_to_boss('梦魇兽', object(), '琉璃幻境[副本]',
                                     (100, 100)) is True
    assert calls == ['click', 'lock']


def test_walk_to_boss_aborts_when_left_map():
    task = make_boss_walk_task(('沙狱外', 128, 182))
    task.dungeon_click_minimap_target = lambda corner, rule: True

    def abort():
        raise DungeonAborted('琉璃幻境未通关')

    task.dungeon_notify_uncleared = abort
    try:
        task.dungeon_walk_to_boss('梦魇兽', object(), '琉璃幻境[副本]',
                                  (100, 100), timeout=1)
    except DungeonAborted:
        pass
    else:
        raise AssertionError('DungeonAborted not raised')


def test_try_lock_closes_menu_before_detecting():
    task = DungeonBase.__new__(DungeonBase)
    calls = []
    task.ui_close_menu = lambda: calls.append('close')
    task.screenshot = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
    task.ui_target_attackable = lambda image=None: False
    task.ui_lock_target = lambda: calls.append('lock') or False
    assert task.dungeon_try_lock_target(['梦魔兽']) is False
    assert calls == ['close', 'lock']


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'---- {len(tests)}/{len(tests)} passed ----')
