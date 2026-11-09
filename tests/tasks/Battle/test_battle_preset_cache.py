# -*- coding: utf-8 -*-
"""战斗预设缓存回归测试（不连设备）"""
from types import SimpleNamespace
from unittest.mock import patch

from tasks.Battle import attack as attack_module
from tasks.Battle.attack import BattleAttack


class FakeBattleTask:
    """只测 battle_preset 的缓存，不碰设备/配置系统"""

    def __init__(self):
        self.config = SimpleNamespace(battle=SimpleNamespace())

    def battle_catalog(self):
        return object()

    def battle_task_preset(self):
        return 'preset_2'


def test_battle_preset_cached_per_task_instance():
    task = FakeBattleTask()
    preset = {'id': 'preset_2', 'name': '远程'}
    calls = []

    def fake_resolve(*args, **kwargs):
        calls.append(1)
        return preset

    with patch.object(attack_module, 'resolve_preset', fake_resolve):
        first = BattleAttack.battle_preset(task)
        second = BattleAttack.battle_preset(task)

    assert first is preset
    assert second is preset
    assert len(calls) == 1  # 两次调用只解析一次


def test_battle_config_cache_clear_resets_preset_and_plan():
    task = FakeBattleTask()
    task._battle_preset_cache = {'id': 'old'}
    task._battle_plan_cache = ('old-key', [])
    BattleAttack.battle_config_cache_clear(task)
    assert task._battle_preset_cache is None
    assert task._battle_plan_cache is None
