# -*- coding: utf-8 -*-
"""限时活动-野外雪人 流程辅助测试（不连设备）"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd, TaskFailed
from tasks.LimitedTime.WildSnowman.config import WildSnowmanMap
from tasks.LimitedTime.WildSnowman.script_task import ScriptTask


def _task_with_config(**kwargs):
    values = {
        'map_name': WildSnowmanMap.LINGDAO,
        'battle_coord': '251,175',
        'battle_tolerance': 5,
        'heal_repair_interval_minutes': 40,
        'target_names': '陵鱼,可爱雪人,可愛雪人',
    }
    values.update(kwargs)
    inner = SimpleNamespace(**values)
    task = SimpleNamespace(current_config=lambda: inner, MAP_NAME='域外灵岛')
    return task


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


def test_map_name_reads_configured_enum():
    assert ScriptTask.map_name(_task_with_config()) == '域外灵岛'


def test_map_name_falls_back_for_legacy_config():
    task = SimpleNamespace(
        MAP_NAME='域外灵岛',
        current_config=lambda: SimpleNamespace(),
    )
    assert ScriptTask.map_name(task) == '域外灵岛'


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


def test_acquire_target_aborts_in_place_lock_on_reposition():
    captured = {}

    class FakeAcquire:
        TARGET_SWITCH_MAX = 8
        TARGET_SWITCH_TIMEOUT = 10
        TARGET_WARN_AFTER = 10
        _resume_reposition = True
        _target_fail_count = 0

        def ui_ensure_target_locked(self, **kwargs):
            captured['abort_check'] = kwargs.get('abort_check')
            # 模拟：外面已置位重走，abort_check 命中就立刻收工返回 False
            return not kwargs['abort_check']()

    task = FakeAcquire()
    assert ScriptTask.acquire_target(task, ['可爱雪人']) is False
    assert captured['abort_check']() is True
    # 被复活中断，不算「锁不到目标」，不累加计数、不发等待刷新告警
    assert task._target_fail_count == 0


def test_acquire_target_counts_fail_when_not_repositioning():
    class FakeAcquire:
        TARGET_SWITCH_MAX = 8
        TARGET_SWITCH_TIMEOUT = 10
        TARGET_WARN_AFTER = 10
        _resume_reposition = False
        _target_fail_count = 0

        def ui_ensure_target_locked(self, **kwargs):
            return False

    task = FakeAcquire()
    assert ScriptTask.acquire_target(task, ['可爱雪人']) is False
    assert task._target_fail_count == 1


def test_on_target_lost_aborts_relock_on_reposition():
    calls = []
    task = SimpleNamespace(
        battle_fast_relock=lambda: True,
        battle_relock_target=lambda names, abort_check=None: calls.append('relock') or True,
    )
    callback = ScriptTask.battle_on_target_lost(
        task, ['可爱雪人'], abort_check=lambda: True)
    assert callback() is False
    assert calls == []


def test_on_target_lost_relocks_when_no_reposition():
    calls = []
    task = SimpleNamespace(
        battle_fast_relock=lambda: True,
        battle_relock_target=lambda names, abort_check=None: calls.append('relock') or True,
    )
    callback = ScriptTask.battle_on_target_lost(
        task, ['可爱雪人'], abort_check=lambda: False)
    assert callback() is True
    assert calls == ['relock']


def test_battle_relock_target_aborts_before_clicking():
    clicks = []
    task = SimpleNamespace(
        RELOCK_MAX_SWITCH=3,
        C_TARGET_BUTTON_CLICK=SimpleNamespace(
            coord=lambda: clicks.append('click') or (1, 2)),
    )
    assert ScriptTask.battle_relock_target(task, ['x'], abort_check=lambda: True) is False
    assert clicks == []


def test_battle_target_ok_rejects_attackable_player():
    task = SimpleNamespace(
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '孤舟载雪渡寒江',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['可爱雪人', '陵鱼'], image=None) is False


def test_battle_target_ok_accepts_whitelist():
    task = SimpleNamespace(
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '可愛雪人',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['可爱雪人', '可愛雪人'], image=None) is True


def test_battle_target_ok_false_when_not_attackable():
    task = SimpleNamespace(
        ui_target_attackable=lambda image=None: False,
        ui_target_name=lambda: '可愛雪人',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['可愛雪人']) is False


def test_battle_target_ok_tolerates_empty_name():
    task = SimpleNamespace(
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['可愛雪人']) is True


def test_normal_attack_stops_on_player_without_hitting():
    attacks = []
    task = SimpleNamespace(
        battle_stop_reason=lambda: None,
        screenshot=lambda: 'img',
        battle_target_ok=lambda names, image: False,
        battle_click_attack=lambda: attacks.append('hit'),
        device=SimpleNamespace(sleep=lambda seconds: None),
        NORMAL_ATTACK_INTERVAL=1.0,
    )
    assert ScriptTask.normal_attack_until_lost(task, ['可愛雪人']) == 'target_lost'
    assert attacks == []


def test_normal_attack_hits_valid_then_stops_on_player():
    attacks = []
    results = iter([True, False])
    task = SimpleNamespace(
        battle_stop_reason=lambda: None,
        screenshot=lambda: 'img',
        battle_target_ok=lambda names, image: next(results),
        battle_click_attack=lambda: attacks.append('hit'),
        device=SimpleNamespace(sleep=lambda seconds: None),
        NORMAL_ATTACK_INTERVAL=1.0,
    )
    assert ScriptTask.normal_attack_until_lost(task, ['可愛雪人']) == 'target_lost'
    assert attacks == ['hit']


def test_return_to_position_uses_autopath_when_joystick_fails():
    calls = []

    def fake_move(*args, **kwargs):
        calls.append('joystick')
        return False

    def fake_walk():
        calls.append('autopath')
        return True

    task = SimpleNamespace(
        map_name=lambda: '域外灵岛',
        map_move_read_pos=lambda: ('域外灵岛', 300, 300),
        battle_position=lambda: (251, 175),
        battle_tolerance=lambda: 5,
        map_name_match=lambda text, name: True,
        map_move_to=fake_move,
        walk_to_battle_position=fake_walk,
    )
    assert ScriptTask.return_to_battle_position(task) is True
    assert calls == ['joystick', 'autopath']


def test_walk_to_battle_position_uses_coordinate_navigation():
    calls = []

    def fake_nav(x, y, **kwargs):
        calls.append((x, y, kwargs))
        return ('域外灵岛', 251, 175)

    task = SimpleNamespace(
        map_name=lambda: '域外灵岛',
        battle_position=lambda: (251, 175),
        battle_tolerance=lambda: 5,
        _resume_reposition=False,
        map_current_location=lambda: ('域外灵岛', 120, 80),
        map_name_match=lambda text, name: text == name,
        map_go_to_coordinate=fake_nav,
        MAP_WALK_TIMEOUT=120,
    )
    assert ScriptTask.walk_to_battle_position(task) is True
    assert calls == [(251, 175, {
        'map_name': '域外灵岛',
        'timeout': 120,
        'tolerance': 5,
    })]


def test_walk_to_battle_position_skips_when_already_there():
    calls = []
    task = SimpleNamespace(
        map_name=lambda: '域外灵岛',
        battle_position=lambda: (251, 175),
        battle_tolerance=lambda: 5,
        _resume_reposition=False,
        map_current_location=lambda: ('域外灵岛', 253, 173),
        map_name_match=lambda text, name: text == name,
        map_go_to_coordinate=lambda *args, **kwargs: calls.append(args),
    )
    assert ScriptTask.walk_to_battle_position(task) is True
    assert calls == []


def test_walk_to_battle_position_returns_false_on_task_failed():
    def fake_nav(*args, **kwargs):
        raise TaskFailed('坐标不可达')

    task = SimpleNamespace(
        map_name=lambda: '域外灵岛',
        battle_position=lambda: (251, 175),
        battle_tolerance=lambda: 5,
        _resume_reposition=False,
        map_current_location=lambda: ('域外灵岛', 120, 80),
        map_name_match=lambda text, name: text == name,
        map_go_to_coordinate=fake_nav,
        MAP_WALK_TIMEOUT=120,
    )
    assert ScriptTask.walk_to_battle_position(task) is False


def test_walk_to_battle_position_aborts_after_death():
    def fake_nav(*args, **kwargs):
        task._resume_reposition = True
        return ('域外灵岛', 251, 175)

    task = SimpleNamespace(
        map_name=lambda: '域外灵岛',
        battle_position=lambda: (251, 175),
        battle_tolerance=lambda: 5,
        _resume_reposition=False,
        map_current_location=lambda: ('域外灵岛', 120, 80),
        map_name_match=lambda text, name: text == name,
        map_go_to_coordinate=fake_nav,
        MAP_WALK_TIMEOUT=120,
    )
    assert ScriptTask.walk_to_battle_position(task) is False


class _StopBattleLoop(Exception):
    pass


def test_battle_loop_recognizes_skill_slots_only_once():
    recognized = []
    acquired = []

    def acquire_target(names):
        acquired.append(names)
        if len(acquired) > 2:
            raise _StopBattleLoop
        return True

    task = SimpleNamespace(
        target_names=lambda: ['可爱雪人'],
        stop_for_others=lambda: False,
        stop_because_disabled=lambda: False,
        _resume_reposition=False,
        need_recover=lambda: False,
        acquire_target=acquire_target,
        recognize_skill_slots=lambda: recognized.append('slots') or [0, 1],
        battle_current_slots=lambda: [0, 1],
        battle_skill_loop=lambda *args, **kwargs: 'target_lost',
        battle_stop_reason=lambda image=None: None,
        battle_target_ok=lambda names, image=None: True,
        battle_use_normal_attack=lambda: False,
        battle_on_target_lost=lambda names, abort_check=None: (lambda: False),
        on_cast=lambda casts: None,
        normal_attack_until_lost=lambda names: 'target_lost',
        NORMAL_ATTACK_INTERVAL=1.0,
        SKILL_COOLDOWN_POLL=0.1,
        device=SimpleNamespace(sleep=lambda seconds: None),
        _target_fail_count=0,
        TARGET_WARN_AFTER=10,
        return_to_battle_position=lambda: True,
    )

    with pytest.raises(_StopBattleLoop):
        ScriptTask.battle_loop(task)

    assert len(acquired) == 3
    assert len(recognized) == 1


def test_ensure_clears_death_flag_and_keeps_recover_reset():
    calls = []
    task = SimpleNamespace(
        map_name=lambda: '域外灵岛',
        _resume_reposition=True,
        _reset_recover_after_resume=True,
        _ensure_in_map_and_position_once=lambda reset_recover: (
            calls.append(reset_recover) or True),
        device=SimpleNamespace(sleep=lambda seconds: None),
    )
    assert ScriptTask.ensure_in_map_and_position(task, reset_recover=False) is True
    assert calls == [True]
    assert task._resume_reposition is False
    assert task._reset_recover_after_resume is False


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
