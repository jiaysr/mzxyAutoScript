# -*- coding: utf-8 -*-
"""节奏式技能释放的优先级/跳过/目标校验测试（不连设备）"""
from types import SimpleNamespace

from tasks.Battle.attack import BattleAttack
from tasks.Battle.cooldown import SKILL_COOLING, SKILL_READY


def _task(states, *, stop_after=1, max_checks=None, include_attack=False):
    """构造 battle_skill_loop 需要的假任务，记录每次点击的技能位"""
    calls = []
    attacks = []
    sleeps = []
    checks = {'n': 0}

    def check_stop(image):
        checks['n'] += 1
        if max_checks is not None:
            return 'stop' if checks['n'] >= max_checks else None
        return 'stop' if len(calls) >= stop_after else None

    task = SimpleNamespace(
        battle_release_config=lambda: {
            'clicks': 2, 'click_interval': 0.1, 'skill_interval': 0.1,
        },
        battle_use_normal_attack=lambda: include_attack,
        screenshot=lambda: 'image',
        battle_states_raw=lambda image=None: dict(states),
        battle_click_skill_multi=lambda slot, clicks=None, interval=None: calls.append(slot),
        battle_click_attack=lambda: attacks.append(True),
        device=SimpleNamespace(sleep=lambda seconds: sleeps.append(seconds)),
    )
    return task, calls, attacks, sleeps, check_stop


def test_highest_priority_ready_slot_is_clicked():
    # 技能1 冷却中，技能2/3 冷却好：点技能2（顺序里第一个可用的）
    states = {0: SKILL_COOLING, 1: SKILL_READY, 2: SKILL_READY}
    task, calls, _, _, check_stop = _task(states, stop_after=1)

    assert BattleAttack.battle_skill_loop(
        task, [0, 1, 2], check_stop=check_stop,
        include_attack=False, recheck_pending=False,
    ) == 'stop'
    assert calls == [1]


def test_target_lost_returns_before_clicking():
    task, calls, _, _, check_stop = _task({0: SKILL_READY})

    assert BattleAttack.battle_skill_loop(
        task, [0], check_stop=check_stop,
        target_check=lambda image: False,
        include_attack=False, recheck_pending=False,
    ) == 'target_lost'
    assert calls == []


def test_all_cooling_clicks_normal_attack_only():
    task, calls, attacks, sleeps, check_stop = _task(
        {0: SKILL_COOLING, 1: SKILL_COOLING},
        max_checks=2, include_attack=True,
    )

    assert BattleAttack.battle_skill_loop(
        task, [0, 1], check_stop=check_stop,
        include_attack=True, recheck_pending=False,
    ) == 'stop'
    assert calls == []
    assert attacks == [True]
    assert sleeps == [0.4]  # idle_interval


def test_max_skills_returns_after_click_limit():
    task, calls, _, _, check_stop = _task({0: SKILL_READY}, max_checks=10)

    assert BattleAttack.battle_skill_loop(
        task, [0], check_stop=check_stop,
        include_attack=False, recheck_pending=False, max_skills=1,
    ) == 'skill_limit'
    assert calls == [0]


def test_skill_interval_is_waited_after_each_click():
    task, calls, _, sleeps, check_stop = _task({0: SKILL_READY}, stop_after=1)

    assert BattleAttack.battle_skill_loop(
        task, [0], check_stop=check_stop,
        include_attack=False, recheck_pending=False, skill_interval=0.4,
    ) == 'stop'
    assert calls == [0]
    assert sleeps == [0.4]
