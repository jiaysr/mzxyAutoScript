# -*- coding: utf-8 -*-
"""限时活动-野外雪人 流程辅助测试（不连设备）"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.LimitedTime.WildSnowman.script_task import ScriptTask


def _task_with_config(**kwargs):
    values = {
        'battle_coord': '251,175',
        'battle_tolerance': 5,
        'heal_repair_interval_minutes': 40,
        'target_names': '陵鱼,可爱雪人,可愛雪人',
    }
    values.update(kwargs)
    return SimpleNamespace(current_config=lambda: SimpleNamespace(**values))


def test_battle_preset_reads_own_battle_config_not_dungeon():
    own = SimpleNamespace(battle_preset='snowman_preset')
    task = SimpleNamespace(
        config=SimpleNamespace(
            wild_snowman=SimpleNamespace(battle_config=own),
            dungeon=SimpleNamespace(battle_config=SimpleNamespace(battle_preset='dungeon_preset')),
        ),
        battle_task_battle_config=lambda: own,
    )
    assert ScriptTask.battle_task_battle_config(task) is own
    assert ScriptTask.battle_task_preset(task) == 'snowman_preset'


def test_battle_position_parses_default():
    assert ScriptTask.battle_position(_task_with_config()) == (251, 175)


def test_battle_position_falls_back_on_bad_value():
    task = _task_with_config(battle_coord='bad')
    assert ScriptTask.battle_position(task) == (251, 175)


def test_target_names_splits_variants():
    task = _task_with_config(target_names='陵鱼，可爱雪人,可愛雪人')
    assert ScriptTask.target_names(task) == ['陵鱼', '可爱雪人', '可愛雪人']


def test_need_recover_after_interval():
    task = _task_with_config(heal_repair_interval_minutes=40)
    task._last_recover = datetime.now() - timedelta(minutes=41)
    assert ScriptTask.need_recover(task) is True
    task._last_recover = datetime.now() - timedelta(minutes=39)
    assert ScriptTask.need_recover(task) is False


class FakeFlagsTask:
    def __init__(self):
        self._resume_reposition = False
        self._reset_recover_after_resume = False
        self.device = SimpleNamespace(sleep=lambda seconds: None)


def test_death_resume_repositions_and_resets_timer():
    task = FakeFlagsTask()
    ScriptTask.handle_death_resume(task)
    assert task._resume_reposition is True
    assert task._reset_recover_after_resume is True


def test_disconnect_resume_repositions_without_resetting_timer():
    task = FakeFlagsTask()
    ScriptTask.handle_disconnect_resume(task)
    assert task._resume_reposition is True
    assert task._reset_recover_after_resume is False


class FakeTimer:
    def __init__(self, reached=True):
        self.reached_value = reached

    def reached_and_reset(self):
        return self.reached_value


def test_battle_stop_reason_repositions_when_flag_set():
    task = SimpleNamespace(_resume_reposition=True)
    assert ScriptTask.battle_stop_reason(task) == 'reposition'


def test_return_to_position_uses_autopath_when_joystick_fails():
    calls = []

    def fake_move(*args, **kwargs):
        calls.append('joystick')
        return False

    def fake_walk():
        calls.append('autopath')
        return True

    task = SimpleNamespace(
        MAP_NAME='域外灵岛',
        map_move_read_pos=lambda: ('域外灵岛', 300, 300),
        battle_position=lambda: (251, 175),
        battle_tolerance=lambda: 5,
        map_name_match=lambda text, name: True,
        map_move_to=fake_move,
        walk_to_battle_position=fake_walk,
    )
    assert ScriptTask.return_to_battle_position(task) is True
    assert calls == ['joystick', 'autopath']


def test_stop_for_others_checks_higher_priority():
    task = SimpleNamespace(_yield_timer=FakeTimer(True),
                           higher_priority_task_due=lambda: True)
    assert ScriptTask.stop_for_others(task) is True
    task.higher_priority_task_due = lambda: False
    assert ScriptTask.stop_for_others(task) is False
    task._yield_timer = FakeTimer(False)
    task.higher_priority_task_due = lambda: True
    assert ScriptTask.stop_for_others(task) is False


def test_stop_because_disabled_reloads_config():
    calls = []

    class FakeConfig:
        def should_reload(self):
            return True

        def reload(self):
            calls.append('reload')

        def start_watching(self):
            calls.append('watch')

    config = FakeConfig()
    config.wild_snowman = SimpleNamespace(
        scheduler=SimpleNamespace(enable=False))
    task = SimpleNamespace(_config_timer=FakeTimer(True),
                           config=config,
                           battle_config_cache_clear=lambda: calls.append('clear'))
    assert ScriptTask.stop_because_disabled(task) is True
    assert calls == ['reload', 'watch', 'clear']


class FakeHealerTask:
    DUNGEON_HEALER_DIALOG_TIMEOUT = 2
    DUNGEON_HEALER_INTERACT_AFTER = 0
    DUNGEON_HEALER_MAX_INTERACT = 3
    DUNGEON_HEALER_NAME = '万事通'
    I_DUNGEON_HEALER_DIALOG = 'dialog'

    def __init__(self):
        self.target = '怪物'
        self.dialog = False
        self.locks = 0
        self.attacks = 0
        self._resume_reposition = False
        self.device = SimpleNamespace(sleep=lambda seconds: None)
        self.ocr_name_match = ScriptTask.ocr_name_match

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        return self.dialog and target == self.I_DUNGEON_HEALER_DIALOG

    def ui_target_name(self):
        return self.target

    def ui_lock_target(self):
        self.locks += 1
        if self.target != self.DUNGEON_HEALER_NAME:
            self.target = self.DUNGEON_HEALER_NAME
        return True

    def ui_close_menu(self):
        pass

    def battle_click_attack(self):
        self.attacks += 1
        self.dialog = True


def test_healer_dialog_switches_from_monster_to_healer():
    task = FakeHealerTask()
    assert ScriptTask.dungeon_wait_healer_dialog(task, timeout=2) is True
    assert task.locks >= 1
    assert task.attacks == 1


class FakeRunTask:
    YIELD_CHECK_INTERVAL = ScriptTask.YIELD_CHECK_INTERVAL
    CONFIG_CHECK_INTERVAL = ScriptTask.CONFIG_CHECK_INTERVAL
    YIELD_RESUME_SECONDS = ScriptTask.YIELD_RESUME_SECONDS

    def __init__(self, yield_flag=False):
        self.yield_flag = yield_flag
        self.calls = []
        self.config = SimpleNamespace(start_watching=lambda: self.calls.append('watch'))
        self.device = SimpleNamespace(sleep=lambda seconds: None)
        self._yield_timer = FakeTimer(False)
        self._config_timer = FakeTimer(False)

    def ensure_in_map_and_position(self, reset_recover=False):
        self.calls.append(('enter', reset_recover))
        return True

    def battle_loop(self):
        self.calls.append('battle_loop')
        self._yielded = self.yield_flag

    def set_next_run(self, **kwargs):
        self.calls.append(('schedule', kwargs))


def test_run_yield_schedules_immediately():
    task = FakeRunTask(yield_flag=True)
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    schedule = [item for item in task.calls if isinstance(item, tuple)
                and item[0] == 'schedule'][0][1]
    assert schedule['task'] == 'WildSnowman'
    assert schedule['success'] is None
    assert schedule['server'] is False
    assert isinstance(schedule['target'], datetime)


def test_run_normal_schedules_success():
    task = FakeRunTask()
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    schedule = [item for item in task.calls if isinstance(item, tuple)
                and item[0] == 'schedule'][0][1]
    assert schedule['success'] is True
