# -*- coding: utf-8 -*-
"""域外迷窟 战斗目标名校验测试（不连设备）"""
from types import SimpleNamespace

from tasks.YuwaiMimang.script_task import ScriptTask


def _task(attackable=True, name=''):
    return SimpleNamespace(
        ui_target_attackable=lambda image=None: attackable,
        ui_target_name=lambda: name,
        ocr_name_match=ScriptTask.ocr_name_match,
    )


def test_rejects_attackable_player():
    task = _task(attackable=True, name='孤舟载雪渡寒江')
    assert ScriptTask.battle_target_ok(task, ['小鬼', '鬼王', '鬼将'], image=None) is False


def test_accepts_whitelist():
    task = _task(attackable=True, name='鬼王')
    assert ScriptTask.battle_target_ok(task, ['小鬼', '鬼王', '鬼将'], image=None) is True


def test_false_when_not_attackable():
    task = _task(attackable=False, name='鬼王')
    assert ScriptTask.battle_target_ok(task, ['鬼王']) is False


def test_tolerates_empty_name():
    task = _task(attackable=True, name='')
    assert ScriptTask.battle_target_ok(task, ['鬼王']) is True


def test_empty_whitelist_skips_name_check():
    task = _task(attackable=True, name='孤舟载雪渡寒江')
    assert ScriptTask.battle_target_ok(task, []) is True


def test_empty_whitelist_still_requires_attackable():
    task = _task(attackable=False, name='anything')
    assert ScriptTask.battle_target_ok(task, []) is False