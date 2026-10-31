# -*- coding: utf-8 -*-
"""
世界首领双 boss 配置/分组单测（不连设备）

覆盖：
- 同一时段事件分组
- first_boss 配置决定先打顺序，非法/auto 回落事件顺序
- 后端 schema 暴露 first_boss / first_boss_skills 的 visible_when 与枚举

    toolkit\\python.exe tests\\test_worldboss_pair.py
"""
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.config.config import Config
from tasks.WorldBoss.config import FirstBoss
from tasks.WorldBoss.script_task import ScriptTask, WorldBossDeath

WUQING = {'key': 'wuqing', 'name': '无情'}
JULIAN = {'key': 'julian', 'name': '巨镰'}
FUWANG = {'key': 'fuwang', 'name': '斧王'}
T19 = datetime(2026, 10, 31, 19, 0)
T22 = datetime(2026, 10, 31, 22, 0)


def make_task() -> ScriptTask:
    return ScriptTask(Config('oas1'), SimpleNamespace(image=None))


def test_group_events_by_time():
    groups = ScriptTask.group_events([(WUQING, T19), (JULIAN, T19), (FUWANG, T22)])
    assert [time for time, _ in groups] == [T19, T22]
    assert [boss['key'] for boss in groups[0][1]] == ['wuqing', 'julian']
    assert [boss['key'] for boss in groups[1][1]] == ['fuwang']


def test_pair_order_uses_config():
    task = make_task()
    task.config.world_boss.world_boss_config.first_boss = FirstBoss.JULIAN
    first, second = task.pair_order([WUQING, JULIAN])
    assert first['key'] == 'julian'
    assert second['key'] == 'wuqing'


def test_pair_order_falls_back():
    task = make_task()
    task.config.world_boss.world_boss_config.first_boss = FirstBoss.AUTO
    first, second = task.pair_order([WUQING, JULIAN])
    assert first['key'] == 'wuqing'
    assert second['key'] == 'julian'


def test_pair_schema_exposes_conditional_fields():
    fields = {item['name']: item
              for item in Config('oas1').model.script_task('WorldBoss')['world_boss_config']}
    assert fields['first_boss']['visible_when'] == 'world_boss_dual'
    assert fields['first_boss_skills']['visible_when'] == 'world_boss_dual'
    assert 'auto' in fields['first_boss']['enumEnum']
    assert fields['first_boss_skills']['type'] == 'integer'


def test_simultaneous_flow_returns_to_first():
    """A 打 N 技能 -> B 结束 -> A 还没结束，回 A"""
    task = make_task()
    task.config.world_boss.world_boss_config.first_boss = FirstBoss.WUQING
    task.config.world_boss.world_boss_config.first_boss_skills = 3
    task.done_events = set()
    calls = []

    def fake(boss, pair, state, event_time, deadline, max_skills=None, lock_timeout=None,
             cast_counter=None):
        calls.append((boss['key'], max_skills))
        if boss['key'] == 'wuqing':
            if max_skills:
                return 'skill_limit'
            state['wuqing'] = True
            return 'finished'
        if boss['key'] == 'julian':
            state['julian'] = True
            return 'finished'
        return 'timeout'

    task.pair_fight_boss = fake
    task.simultaneous_flow([WUQING, JULIAN], datetime.now() - timedelta(minutes=1))
    assert calls == [('wuqing', 3), ('julian', None), ('wuqing', None)]


def test_simultaneous_flow_switches_when_first_dies():
    """A 中途结束 -> 直接打 B，不回 A"""
    task = make_task()
    task.config.world_boss.world_boss_config.first_boss = FirstBoss.WUQING
    task.config.world_boss.world_boss_config.first_boss_skills = 3
    task.done_events = set()
    calls = []

    def fake(boss, pair, state, event_time, deadline, max_skills=None, lock_timeout=None,
             cast_counter=None):
        calls.append((boss['key'], max_skills))
        state[boss['key']] = True
        return 'finished'

    task.pair_fight_boss = fake
    task.simultaneous_flow([WUQING, JULIAN], datetime.now() - timedelta(minutes=1))
    assert calls == [('wuqing', 3), ('julian', None)]


def patch_device_deps(task: ScriptTask) -> None:
    """把双 boss 流程里除技能循环之外的设备依赖全部打桩"""
    task.screenshot = lambda *a, **k: None
    task.battle_plan = lambda *a, **k: [SimpleNamespace(slot=0), SimpleNamespace(slot=1)]
    task.pair_update_finish = lambda *a, **k: []
    task.handle_pair_new = lambda *a, **k: None
    task.ui_target_attackable = lambda *a, **k: True
    task.prepare_boss_map = lambda *a, **k: True
    task.enter_world_boss_page = lambda *a, **k: True
    task.seek_boss = lambda *a, **k: True
    task.wait_until_arrive = lambda *a, **k: True
    task.pair_lock_boss = lambda *a, **k: 'locked'


def test_pair_attack_loop_resumes_after_death():
    """死亡前已放 2 个技能，复活重试只补剩下的 1 个"""
    task = make_task()
    task.done_events = set()
    patch_device_deps(task)
    pair = [WUQING, JULIAN]
    state = {'wuqing': False, 'julian': False}
    deadline = datetime.now() + timedelta(minutes=5)
    counter = {'casts': 0}
    seen = []
    rounds = {'n': 0}

    def fake_loop(slots, **kwargs):
        seen.append(kwargs.get('max_skills'))
        rounds['n'] += 1
        if rounds['n'] == 1:
            kwargs['on_cast'](2)
            raise WorldBossDeath('attack')
        kwargs['on_cast'](1)
        return 'skill_limit'

    task.battle_skill_loop = fake_loop

    try:
        task.pair_attack_loop(WUQING, pair, state, T19, deadline,
                              max_skills=3, cast_counter=counter)
    except WorldBossDeath:
        pass
    else:
        raise AssertionError('死亡应抛给 pair_fight_boss 重试，而不是被吞掉')
    assert counter['casts'] == 2

    result = task.pair_attack_loop(WUQING, pair, state, T19, deadline,
                                   max_skills=3, cast_counter=counter)
    assert seen == [3, 1], seen
    assert result == 'skill_limit'
    assert counter['casts'] == 3


def test_pair_attack_loop_skips_when_quota_used():
    """配额已满（死亡刚好发生在最后一次释放后）直接返回，不再进场"""
    task = make_task()
    task.done_events = set()
    patch_device_deps(task)
    task.battle_skill_loop = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('配额已满不应再攻击'))

    result = task.pair_attack_loop(WUQING, [WUQING, JULIAN],
                                   {'wuqing': False, 'julian': False}, T19,
                                   datetime.now() + timedelta(minutes=5),
                                   max_skills=3, cast_counter={'casts': 3})
    assert result == 'skill_limit'


def test_pair_fight_boss_keeps_counter_across_death():
    """pair_fight_boss 捕获死亡后重试，计数不归零"""
    task = make_task()
    task.done_events = set()
    patch_device_deps(task)
    pair = [WUQING, JULIAN]
    state = {'wuqing': False, 'julian': False}
    counter = {'casts': 0}
    rounds = {'n': 0}

    def fake_loop(slots, **kwargs):
        rounds['n'] += 1
        kwargs['on_cast'](1)
        if rounds['n'] == 1:
            raise WorldBossDeath('attack')
        return 'skill_limit'

    task.battle_skill_loop = fake_loop
    result = task.pair_fight_boss(WUQING, pair, state, T19,
                                  datetime.now() + timedelta(minutes=5),
                                  max_skills=2, cast_counter=counter)
    assert result == 'skill_limit'
    assert rounds['n'] == 2
    assert counter['casts'] == 2


def test_reward_only_for_current_boss():
    """双 boss 时只有当前 boss 的结束模板触发奖励面板"""
    task = make_task()
    rewards = []
    task.handle_finish = lambda boss: rewards.append(boss['key'])

    task.handle_pair_new([WUQING, JULIAN], current_boss=WUQING)
    assert rewards == ['wuqing']

    rewards.clear()
    task.handle_pair_new([WUQING, JULIAN], current_boss=WUQING, allow_reward=False)
    assert rewards == []


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
