# -*- coding: utf-8 -*-
"""队列式技能释放的优先级/重试行为测试（不连设备）"""
from types import SimpleNamespace

from tasks.Battle.attack import BattleAttack
from tasks.Battle.cooldown import SKILL_COOLING, SKILL_READY


def _task(states, results):
    """构造 battle_skill_loop 需要的假任务，记录每次释放的技能位"""
    calls = []
    results = iter(results)

    def release(slot, **kwargs):
        calls.append(slot)
        return next(results, 'failed')

    def check_stop(image):
        # 每个技能最多释放两次后收工，避免测试依赖外部超时
        return 'stop' if len(calls) >= 2 else None

    task = SimpleNamespace(
        battle_release_config=lambda: {
            'clicks': 2, 'click_interval': 0.1,
            'attempts': 3, 'cooldown_timeout': 1.2,
        },
        battle_use_normal_attack=lambda: False,
        screenshot=lambda: 'image',
        battle_skills_state=lambda image=None: dict(states),
        battle_skill_release=release,
        device=SimpleNamespace(sleep=lambda seconds: None),
    )
    return task, calls, check_stop


def test_failed_highest_priority_skill_is_retried_before_lower_slots():
    # 技能1、技能2 都冷却好，技能3 冷却中；技能1 连续失败也应继续点技能1
    task, calls, check_stop = _task(
        {0: SKILL_READY, 1: SKILL_READY, 2: SKILL_COOLING},
        ['failed', 'failed', 'failed'],
    )

    assert BattleAttack.battle_skill_loop(
        task, [0, 1, 2], check_stop=check_stop,
        include_attack=False, recheck_pending=False,
    ) == 'stop'
    assert calls == [0, 0]


def test_lower_slot_is_used_when_higher_slot_is_cooling():
    task, calls, check_stop = _task(
        {0: SKILL_COOLING, 1: SKILL_READY, 2: SKILL_COOLING},
        ['failed', 'failed'],
    )

    assert BattleAttack.battle_skill_loop(
        task, [0, 1, 2], check_stop=check_stop,
        include_attack=False, recheck_pending=False,
    ) == 'stop'
    assert calls == [1, 1]


def test_single_ready_skill_keeps_retrying_until_cooldown_or_stop():
    task, calls, check_stop = _task(
        {0: SKILL_READY, 1: SKILL_COOLING},
        ['failed', 'failed', 'failed'],
    )

    assert BattleAttack.battle_skill_loop(
        task, [0, 1], check_stop=check_stop,
        include_attack=False, recheck_pending=False,
    ) == 'stop'
    assert calls == [0, 0]
