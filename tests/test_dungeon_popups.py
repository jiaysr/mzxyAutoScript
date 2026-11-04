# -*- coding: utf-8 -*-
"""副本结算弹窗关闭顺序与完成弹窗变体单测（不连设备）"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Dungeon.assets import DungeonAssets
from tasks.Dungeon.common import DungeonBase


def make_task(finish_ok=True, reward_ok=True):
    task = DungeonBase.__new__(DungeonBase)
    calls = []
    task.dungeon_close_finish = lambda: calls.append('finish') or finish_ok
    task.dungeon_close_boss_reward = lambda: calls.append('reward') or reward_ok
    return task, calls


def test_result_popups_close_finish_then_reward():
    task, calls = make_task()
    assert task.dungeon_close_result_popups() is True
    assert calls == ['finish', 'reward']


def test_result_popups_fail_if_any_remains():
    task, calls = make_task(reward_ok=False)
    assert task.dungeon_close_result_popups() is False
    assert calls == ['finish', 'reward']


def test_finish_suffix_covers_countdown_variants():
    task = DungeonBase.__new__(DungeonBase)
    suffix = DungeonAssets.I_DUNGEON_FINISH_SUFFIX.image
    blank = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert task.dungeon_finish_visible(blank) is False
    # 30秒/15秒文案更短，后缀整体左右平移，仍应在搜索区内命中
    for x in (580, 600, 620):
        image = blank.copy()
        image[330:330 + suffix.shape[0], x:x + suffix.shape[1]] = suffix
        assert task.dungeon_finish_visible(image) is True


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'---- {len(tests)}/{len(tests)} passed ----')
