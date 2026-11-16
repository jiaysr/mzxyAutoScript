# -*- coding: utf-8 -*-
"""域外迷窟 打怪主循环测试（不连设备）"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tasks.YuwaiMimang.script_task import ScriptTask


class _StopLoop(Exception):
    pass


def test_preset_battle_loop_no_acquire_and_uses_relock():
    loops = []
    recognized = []

    def battle_skill_loop(*args, **kwargs):
        loops.append(kwargs)
        if len(loops) > 2:
            raise _StopLoop
        return 'timeout'

    task = SimpleNamespace(
        battle_target_names=lambda: ['小鬼'],
        battle_use_normal_attack=lambda: False,
        battle_preset=lambda: {'name': '默认预设'},
        reset_battle_progress=lambda end: None,
        reset_records=lambda: None,
        recognize_skill_slots=lambda: recognized.append('slots') or [0, 1],
        battle_current_slots=lambda: [0, 1],
        battle_skill_loop=battle_skill_loop,
        battle_stop_reason=lambda image=None: None,
        battle_target_ok=lambda names, image=None: True,
        battle_relock_once=lambda: False,
        NORMAL_ATTACK_INTERVAL=1.0,
        RECOVER_EVERY_CASTS=6,
        device=SimpleNamespace(sleep=lambda seconds: None),
        recover_in_battle=lambda coord, tolerance: True,
        need_repair=lambda cfg, last_repair, end: False,
    )

    with pytest.raises(_StopLoop):
        ScriptTask.preset_battle_loop(
            task, (304, 76), 2, datetime.now() + timedelta(minutes=1), ['小鬼'], None)

    assert len(recognized) == 1
    assert len(loops) == 3
    assert loops[0]['on_target_lost'] is task.battle_relock_once
    assert task._battle_coord == (304, 76)
    assert task._battle_tolerance == 2


def test_relock_recover_uses_battle_coord():
    calls = []
    task = SimpleNamespace(
        _battle_coord=(304, 76),
        _battle_tolerance=2,
        recover_in_battle=lambda coord, tolerance: calls.append((coord, tolerance)) or True,
    )
    assert ScriptTask.battle_relock_recover_position(task) is True
    assert calls == [((304, 76), 2)]


def test_relock_recover_never_blocks_grinding():
    task = SimpleNamespace(
        _battle_coord=(304, 76),
        _battle_tolerance=2,
        recover_in_battle=lambda coord, tolerance: False,
    )
    assert ScriptTask.battle_relock_recover_position(task) is True
