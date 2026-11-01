# -*- coding: utf-8 -*-
"""
世界首领死亡恢复单测（不连设备）

覆盖：
- handle_death 复活后抛出 WorldBossDeath(stage)
- 单 boss 攻击中死亡后，从寻路阶段重试并继续攻击

    toolkit\\python.exe tests\\test_worldboss_death.py
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
from tasks.WorldBoss.script_task import (ScriptTask, WorldBossDeath,
                                        WorldBossFinished)

BOSS = {'key': 'manchui', 'name': '蛮锤', 'target_map': '牧野', 'target_coord': (272, 70)}


def make_task() -> ScriptTask:
    device = SimpleNamespace(image=None, sleep=lambda seconds: None)
    return ScriptTask(Config('oas1'), device)


def test_handle_death_raises_stage():
    task = make_task()
    revived = []
    task.appear = lambda target: True
    task.revive = lambda: revived.append(True) or True
    task._death_stage = 'attack'

    try:
        task.handle_death()
        assert False, 'should raise WorldBossDeath'
    except WorldBossDeath as e:
        assert e.stage == 'attack'
    assert revived == [True]


def test_handle_death_ignores_no_dialog():
    task = make_task()
    task.appear = lambda target: False
    task.handle_death()  # 不抛异常


def test_attack_death_retries_navigation():
    task = make_task()
    task.done_events = set()
    calls = {'prepare': 0, 'attack': 0, 'finish': 0}

    task.prepare_boss_map = lambda boss: calls.__setitem__('prepare', calls['prepare'] + 1) or True
    task.enter_world_boss_page = lambda timeout=40: True
    task.seek_boss = lambda boss: True
    task.wait_until_arrive = lambda boss, timeout=180, finish_rule=None: 'arrived'
    task.finish_rule = lambda boss: None

    def lock_boss(boss, event_time, finish_rule, deadline):
        return 'locked'

    def attack_boss(boss, finish_rule, deadline):
        calls['attack'] += 1
        if calls['attack'] == 1:
            raise WorldBossDeath('attack')
        return 'finished'

    task.lock_boss = lock_boss
    task.attack_boss = attack_boss
    task.handle_finish = lambda boss: calls.__setitem__('finish', calls['finish'] + 1)

    event_time = datetime.now()
    task.world_boss_flow(BOSS, event_time)
    assert calls == {'prepare': 2, 'attack': 2, 'finish': 1}, calls
    assert task.event_key_for(BOSS, event_time) in task.done_events


def test_death_after_finish_message_stops_without_repatch():
    """死亡期间首领已被打死：复活后立刻补查到结束消息，不再重新寻路"""
    task = make_task()
    task.done_events = set()
    calls = {'prepare': 0, 'attack': 0, 'finish': 0}

    task.prepare_boss_map = lambda boss: calls.__setitem__('prepare', calls['prepare'] + 1) or True
    task.enter_world_boss_page = lambda timeout=40: True
    task.seek_boss = lambda boss: True
    task.wait_until_arrive = lambda boss, timeout=180, finish_rule=None: 'arrived'
    task.finish_rule = lambda boss: object()
    task.check_finish = lambda boss, finish_rule, stage='': True

    task.lock_boss = lambda boss, event_time, finish_rule, deadline: 'locked'

    def attack_boss(boss, finish_rule, deadline):
        calls['attack'] += 1
        raise WorldBossDeath('attack')

    task.attack_boss = attack_boss
    task.handle_finish = lambda boss: calls.__setitem__('finish', calls['finish'] + 1)

    event_time = datetime.now()
    task.world_boss_flow(BOSS, event_time)
    assert calls == {'prepare': 1, 'attack': 1, 'finish': 1}, calls
    assert task.event_key_for(BOSS, event_time) in task.done_events


def test_watch_finish_raises_on_match():
    """首领出现后监听生效：任意一次截图命中结束模板就抛 WorldBossFinished"""
    task = make_task()
    task.device.screenshot = lambda: None
    task.device.image = object()
    task.handle_death = lambda: None
    task.handle_captcha = lambda: None
    task.handle_global_popup = lambda: None
    task.finish_confirmed = lambda boss, rule, image: True

    task.watch_finish_start(BOSS, object(), datetime.now() + timedelta(seconds=10))
    try:
        task.screenshot()
        assert False, 'should raise WorldBossFinished'
    except WorldBossFinished as e:
        assert e.key == BOSS['key']


def test_watch_finish_ignores_after_deadline():
    """超过 deadline 不再监听（截图正常返回）"""
    task = make_task()
    task.device.screenshot = lambda: None
    task.device.image = object()
    task.handle_death = lambda: None
    task.handle_captcha = lambda: None
    task.handle_global_popup = lambda: None
    task.finish_confirmed = lambda boss, rule, image: True

    task.watch_finish_start(BOSS, object(), datetime.now() - timedelta(seconds=1))
    assert task.screenshot() is task.device.image


def test_lock_finish_marks_event_done():
    """锁定阶段监听到结束：标记完成并处理归属奖，不再攻击"""
    task = make_task()
    task.done_events = set()
    calls = {'finish': 0}
    task.prepare_boss_map = lambda boss: True
    task.enter_world_boss_page = lambda timeout=40: True
    task.seek_boss = lambda boss: True
    task.wait_until_arrive = lambda boss, timeout=180, finish_rule=None: 'arrived'
    task.finish_rule = lambda boss: None

    def lock_boss(boss, event_time, finish_rule, deadline):
        raise WorldBossFinished(boss['key'])

    task.lock_boss = lock_boss
    task.attack_boss = lambda boss, finish_rule, deadline: 'timeout'
    task.handle_finish = lambda boss: calls.__setitem__('finish', calls['finish'] + 1)

    event_time = datetime.now()
    task.world_boss_flow(BOSS, event_time)
    assert calls['finish'] == 1
    assert task.event_key_for(BOSS, event_time) in task.done_events


def test_lock_boss_no_target_branch_runs():
    """一直锁不到目标时循环不报错（降噪分支的变量初始化）"""
    task = make_task()
    task.reset_records = lambda: None
    task.wait_until_spawn = lambda event_time: None
    task.collapse_menu = lambda: None
    task.screenshot = lambda: None
    task.finish_confirmed = lambda boss, rule, image: False
    task.ui_target_locked = lambda image=None: False
    task.appear_then_click = lambda *args, **kwargs: None
    task.TARGET_LOCK_SWITCH_INTERVAL = 0
    task.NO_TARGET_HINT = 0  # 立刻走「可能已被击败」的提示分支

    deadline = datetime.now() + timedelta(seconds=0.5)
    assert task.lock_boss(BOSS, datetime.now(), object(), deadline) == 'failed'


def test_pair_lock_boss_no_target_branch_runs():
    """双 boss 锁定同样走降噪分支，且按 lock_timeout 退出"""
    task = make_task()
    task.reset_records = lambda: None
    task.wait_until_spawn = lambda event_time: None
    task.collapse_menu = lambda: None
    task.screenshot = lambda: None
    task.pair_update_finish = lambda image, pair, state, event_time: []
    task.handle_pair_new = lambda newly, boss, allow_reward=True: None
    task.pair_all_done = lambda state: False
    task.ui_target_locked = lambda image=None: False
    task.appear_then_click = lambda *args, **kwargs: None
    task.TARGET_LOCK_SWITCH_INTERVAL = 0
    task.NO_TARGET_HINT = 0

    deadline = datetime.now() + timedelta(seconds=2)
    state = {BOSS['key']: False}
    result = task.pair_lock_boss(BOSS, [BOSS], state, datetime.now(), deadline,
                                 lock_timeout=0.2)
    assert result == 'lock_timeout'


def test_wait_until_arrive_reports_finish_while_navigating():
    """走位过程中命中结束模板，直接返回 finished，不再等到达"""
    task = make_task()
    task.reset_records = lambda: None
    task.map_current_location = lambda: ('万剑冢', 100, 100)
    task.finish_confirmed = lambda boss, rule, image: True
    task.device.image = object()

    assert task.wait_until_arrive(BOSS, timeout=5, finish_rule=object()) == 'finished'


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
