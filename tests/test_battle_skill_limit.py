# -*- coding: utf-8 -*-
"""
battle_skill_loop(max_skills=...) 单测：按「成功进入冷却」计数

    toolkit\\python.exe tests\\test_battle_skill_limit.py
"""
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.attack import BattleAttack
from tasks.Battle.cooldown import SKILL_COOLING, SKILL_READY


class FakeSkillHost:
    def __init__(self):
        self.cooling = set()
        self.casts = []
        self.attacks = 0
        self.device = self

    def screenshot(self):
        return object()

    def sleep(self, seconds):
        pass

    def battle_use_normal_attack(self):
        return False

    def battle_skill_release(self, slot, image=None, target_check=None,
                             max_attempts=3, cooldown_timeout=1.2, poll_interval=0.1):
        self.cooling.add(slot)
        self.casts.append(slot)
        return 'cooled'

    def battle_skills_state(self, image=None):
        return {slot: SKILL_COOLING if slot in self.cooling else SKILL_READY
                for slot in range(3)}

    def battle_click_attack(self):
        self.attacks += 1


def test_max_skills_counts_successful_casts():
    host = FakeSkillHost()
    result = BattleAttack.battle_skill_loop(host, [0, 1, 2], max_skills=2)
    assert result == 'skill_limit'
    assert host.casts == [0, 1], host.casts


def test_without_limit_keeps_going_until_stop():
    host = FakeSkillHost()
    calls = {'n': 0}

    def stop(image):
        calls['n'] += 1
        return 'done' if len(host.casts) >= 3 else None

    result = BattleAttack.battle_skill_loop(host, [0, 1, 2], check_stop=stop)
    assert result == 'done'
    assert host.casts == [0, 1, 2], host.casts


def test_on_target_lost_relocks_and_continues():
    """目标死了：on_target_lost 补锁成功就接着打，失败才结束循环"""
    class LostHost(FakeSkillHost):
        def __init__(self):
            super().__init__()
            self.releases = 0

        def battle_skill_release(self, slot, image=None, target_check=None,
                                 max_attempts=3, cooldown_timeout=1.2, poll_interval=0.1):
            self.releases += 1
            return 'target_lost'

    host = LostHost()
    calls = {'n': 0}

    def relock():
        calls['n'] += 1
        return calls['n'] <= 2   # 前两次补锁成功，第三次失败

    result = BattleAttack.battle_skill_loop(host, [0, 1], on_target_lost=relock)
    assert result == 'target_lost', result
    assert host.releases == 3, f'补锁成功时应留在循环里，实际释放 {host.releases} 次'
    assert calls['n'] == 3, calls


def test_without_relock_callback_stops_immediately():
    """不传 on_target_lost：保持旧行为（目标一丢就返回），不影响世界首领/仙府"""
    class LostHost(FakeSkillHost):
        def __init__(self):
            super().__init__()
            self.releases = 0

        def battle_skill_release(self, slot, image=None, target_check=None,
                                 max_attempts=3, cooldown_timeout=1.2, poll_interval=0.1):
            self.releases += 1
            return 'target_lost'

    host = LostHost()
    result = BattleAttack.battle_skill_loop(host, [0, 1])
    assert result == 'target_lost'
    assert host.releases == 1, host.releases


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
