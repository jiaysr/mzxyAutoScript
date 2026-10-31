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
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.config.config import Config
from tasks.WorldBoss.script_task import ScriptTask, WorldBossDeath

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
    task.wait_until_arrive = lambda boss, timeout=180: True
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
