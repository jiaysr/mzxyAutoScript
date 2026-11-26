# -*- coding: utf-8 -*-
"""世界首领攻击阶段的目标名单校验（不连设备）

目标丢失但首领还没结束时，游戏可能自动转锁到旁边的玩家/小怪。
攻击循环只看「可攻击」会继续放技能，所以这里每帧再校验一次名字：
锁到的不是首领 -> 立即停手，交给外层重新锁定，不按首领已结束处理。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from tasks.WorldBoss.script_task import BOSS_LIST, ScriptTask

MANCHUI = next(boss for boss in BOSS_LIST if boss['key'] == 'manchui')
WUQING = next(boss for boss in BOSS_LIST if boss['key'] == 'wuqing')
JULIAN = next(boss for boss in BOSS_LIST if boss['key'] == 'julian')


def make_state_task(attackable=True, name_ok=True, seen_names=None):
    def target_ok(names, image=None):
        if seen_names is not None:
            seen_names.append(names)
        return name_ok

    return SimpleNamespace(
        ui_target_attackable=lambda image=None: attackable,
        target_keywords=lambda boss: ['蛮锤'],
        battle_target_ok=target_ok,
    )


# ---------------------------------------------------------------- 每帧目标校验
def test_attack_target_state_no_target_is_lost():
    fake = make_state_task(attackable=False)
    assert ScriptTask.attack_target_state(fake, MANCHUI, object()) == 'target_lost'


def test_attack_target_state_other_target_is_wrong():
    seen = []
    fake = make_state_task(attackable=True, name_ok=False, seen_names=seen)
    assert ScriptTask.attack_target_state(fake, MANCHUI, object()) == 'target_wrong'
    # 名字校验用的就是首领关键字
    assert seen == [['蛮锤']]


def test_attack_target_state_matching_name_keeps_attacking():
    fake = make_state_task(attackable=True, name_ok=True)
    assert ScriptTask.attack_target_state(fake, MANCHUI, object()) is None


# ---------------------------------------------------------------- 单 boss
def test_battle_stop_reason_stops_on_other_target():
    fake = SimpleNamespace(
        reset_records=lambda: None,
        finish_confirmed=lambda boss, rule, image: False,
        ui_target_attackable=lambda image=None: True,
        target_keywords=lambda boss: ['蛮锤'],
        battle_target_ok=lambda names, image=None: False,
    )
    fake.attack_target_state = lambda boss, image: ScriptTask.attack_target_state(
        fake, boss, image)
    assert ScriptTask.battle_stop_reason(fake, object(), MANCHUI, None) == 'target_wrong'


def test_attack_boss_does_not_wait_finish_after_wrong_target():
    """锁到玩家不是目标丢失：不该原地等 30s 结束消息，也不该按结束处理"""
    waits = []
    fake = SimpleNamespace(
        screenshot=lambda: object(),
        battle_plan_slots=lambda: [],
        battle_skill_loop=lambda *args, **kwargs: 'target_wrong',
        wait_finish_template=lambda *args, **kwargs: waits.append(args) or True,
    )
    deadline = datetime.now() + timedelta(minutes=5)

    assert ScriptTask.attack_boss(fake, MANCHUI, None, deadline) == 'target_wrong'
    assert waits == []


# ---------------------------------------------------------------- 双 boss
def test_pair_attack_loop_stops_without_marking_finished_on_other_target():
    calls = []
    fake = SimpleNamespace(
        screenshot=lambda: object(),
        battle_plan_slots=lambda: [0],
        # 直接执行战斗循环的 stop_check，模拟第一帧就锁到玩家
        battle_skill_loop=lambda slots, **kwargs: kwargs['check_stop']('image') or 'stop',
        pair_update_finish=lambda *args, **kwargs: [],
        handle_pair_new=lambda *args, **kwargs: None,
        pair_all_done=lambda state: False,
        ui_target_attackable=lambda image=None: True,
        target_keywords=lambda boss: ['蛮锤'],
        battle_target_ok=lambda names, image=None: False,
        wait_pair_finish=lambda *args, **kwargs: calls.append('wait_finish') or 'finished',
    )
    fake.attack_target_state = lambda boss, image: ScriptTask.attack_target_state(
        fake, boss, image)
    state = {'manchui': False}

    result = ScriptTask.pair_attack_loop(
        fake, MANCHUI, [MANCHUI], state, datetime.now(),
        datetime.now() + timedelta(minutes=5),
    )

    assert result == 'target_wrong'
    # 不能当成「首领已死」把 state 置 True，也不能去等结束消息
    assert state['manchui'] is False
    assert 'wait_finish' not in calls


def test_simultaneous_flow_retries_wrong_target_instead_of_switching():
    """锁到玩家只是停手：先打的首领重新锁定继续，第二个首领也原地重试"""
    class FakePairRetry(ScriptTask):
        def __init__(self):
            self.config = SimpleNamespace(world_boss=SimpleNamespace(
                world_boss_config=SimpleNamespace(first_boss='wuqing',
                                                  first_boss_skills=1)))
            self.done_events = set()
            self.left_events = set()
            self._death_stage = None
            self.calls = []
            self.results = {
                'wuqing': ['target_wrong', 'skill_limit', 'finished'],
                'julian': ['target_wrong', 'finished'],
            }
            self.device = SimpleNamespace(sleep=lambda seconds: None)

        def pair_fight_boss(self, boss, pair, state, event_time, deadline,
                            max_skills=None, lock_timeout=None, cast_counter=None):
            self.calls.append(('fight', boss['key'], max_skills))
            result = self.results[boss['key']].pop(0)
            if max_skills:
                if result == 'skill_limit' and cast_counter is not None:
                    cast_counter['casts'] = max_skills
                return result
            if result == 'finished':
                state[boss['key']] = True
                self.mark_event_done(boss, event_time)
            return result

    fake = FakePairRetry()
    event_time = datetime.now() - timedelta(seconds=1)
    ScriptTask.simultaneous_flow(fake, [WUQING, JULIAN], event_time)

    assert fake.calls == [
        ('fight', 'wuqing', 1),      # 锁到玩家 -> 不算打够技能数
        ('fight', 'wuqing', 1),      # 重新锁定继续打满 1 个技能
        ('fight', 'julian', None),
        ('fight', 'julian', None),   # 第二个也锁到玩家 -> 原地重试
        ('fight', 'wuqing', None),   # 回到先打的首领打到结束
    ]
