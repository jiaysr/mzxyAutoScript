# -*- coding: utf-8 -*-
"""
battle_attack_loop() 单测（不连设备）

用一个假的宿主类（只实现循环需要的方法）验证「优先级调度」行为：
- 按优先级从高到低放；放完一个确认进冷却才轮到下一个
- 高优先级技能冷却好了会插队
- 每个技能默认连点 3 次
- 点了没进冷却（超时）就先跳过它，别死磕
- 全在冷却时只补普通攻击
- 冷却状态判不出来（没基准素材）的技能，点过之后晾一会儿，不会反复点
- stop_check 命中就退出

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_attack_loop.py
"""
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.attack import BattleAttack
from tasks.Battle.config import Battle
from tasks.Battle.cooldown import SKILL_COOLING, SKILL_READY, SKILL_UNKNOWN
from tasks.Battle.planner import PlanEntry


class FakeDevice:
    def __init__(self) -> None:
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(round(float(seconds), 3))


class FakeHost(BattleAttack):
    """只实现 battle_attack_loop 需要的东西"""

    def __init__(self, plan, clicks_to_cool=3, never_cool=False, unknown_slots=()):
        self.config = SimpleNamespace(battle=Battle())
        self.device = FakeDevice()
        self.plan = list(plan)
        self.clicks_to_cool = clicks_to_cool
        self.never_cool = never_cool
        self.cooling = set()
        self.unknown = set(unknown_slots)
        self.clicks = []        # 技能点击顺序（含连点）
        self.attacks = 0        # 普通攻击次数
        self.shots = 0
        self.events = []        # ('cast', slot) / ('attack', None)
        self._per_slot = {}
        self.on_group = None    # 每点完一组（clicks_to_cool 次）的回调

    # ---- 画面/状态
    def screenshot(self):
        self.shots += 1
        return None

    def battle_plan(self, preset=None):
        return self.plan

    def battle_skills_state(self):
        return {entry.slot: self.state_of(entry.slot) for entry in self.plan}

    def battle_skill_state(self, slot):
        return self.state_of(slot)

    def state_of(self, slot):
        if slot in self.cooling:
            return SKILL_COOLING
        if slot in self.unknown:
            return SKILL_UNKNOWN
        return SKILL_READY

    # ---- 点击
    def battle_click_skill(self, slot):
        self.clicks.append(slot)
        count = self._per_slot.get(slot, 0) + 1
        self._per_slot[slot] = count
        if count % self.clicks_to_cool == 0:
            self.events.append(('cast', slot))
            if not self.never_cool:
                self.cooling.add(slot)
            if self.on_group is not None:
                self.on_group(self, slot)

    def battle_click_attack(self):
        self.attacks += 1
        self.events.append(('attack', None))


def make_plan(count=3):
    return [PlanEntry(slot=i, skill_id=f's{i}', name=f'技能{i + 1}', priority=i + 1)
            for i in range(count)]


def cast_count(host):
    return sum(1 for kind, _ in host.events if kind == 'cast')


def run_loop(host, casts=None, max_iterations=200, **kwargs):
    """跑到放了 [casts] 次技能（或迭代太多次）就停"""
    state = {'calls': 0}

    def stop():
        state['calls'] += 1
        return (casts is not None and cast_count(host) >= casts) or \
            state['calls'] > max_iterations

    options = dict(cooldown_timeout=0.05, poll_interval=0.0, stop_check=stop)
    options.update(kwargs)
    host.battle_attack_loop(**options)


# --------------------------------------------------------------------- 用例
def test_priority_order():
    """按优先级依次放：A -> B -> C，每个放完确认进冷却才轮到下一个"""
    host = FakeHost(make_plan(3))
    run_loop(host, casts=3)
    assert host.clicks == [0, 0, 0, 1, 1, 1, 2, 2, 2], host.clicks
    assert host.events == [('cast', 0), ('cast', 1), ('cast', 2)], host.events


def test_insert_higher_priority():
    """高优先级技能冷却好了要插队：A、B 放完后 A 冷却好了，下一个应该是 A 而不是 C"""
    host = FakeHost(make_plan(3))

    def on_group(h, slot):
        if slot == 1:
            # B 放完时，A 的冷却刚好结束
            h.cooling.discard(0)

    host.on_group = on_group
    run_loop(host, casts=3)
    order = [slot for kind, slot in host.events if kind == 'cast']
    assert order == [0, 1, 0], f'应该插队放 A，实际 {order}'


def test_clicks_per_skill():
    """每个技能默认连点 3 次；可以改成 4 次"""
    host = FakeHost(make_plan(2))
    run_loop(host, casts=1, clicks_per_skill=3, click_interval=0.15)
    assert host.clicks == [0, 0, 0]
    assert host.device.sleeps[:2] == [0.15, 0.15], host.device.sleeps

    host2 = FakeHost(make_plan(2), clicks_to_cool=4)
    run_loop(host2, casts=1, clicks_per_skill=4)
    assert host2.clicks == [0, 0, 0, 0]


def test_moves_on_when_never_cools():
    """点了 3 次还是没进冷却：先跳过它，继续放下一个，别死磕"""
    host = FakeHost(make_plan(2), never_cool=True)
    run_loop(host, casts=2, cooldown_timeout=0.02)
    order = [slot for kind, slot in host.events if kind == 'cast']
    assert order[:2] == [0, 1], f'第一个技能没进冷却后应该换下一个，实际 {order}'


def test_attacks_when_all_cooling():
    """全在冷却：只补普通攻击，不点任何技能"""
    host = FakeHost(make_plan(3))
    host.cooling = {entry.slot for entry in host.plan}
    run_loop(host, casts=None, max_iterations=5, attack_interval=0.0)
    assert host.clicks == [], f'冷却中不该点技能: {host.clicks}'
    assert host.attacks >= 1, '没技能可放时应该补普通攻击'


def test_unknown_skill_not_spammed():
    """冷却状态判不出来的技能：点一组后先晾着，不能反复点它挡住其它技能"""
    host = FakeHost(make_plan(2), unknown_slots={0})
    run_loop(host, casts=2, unknown_wait=5.0)
    order = [slot for kind, slot in host.events if kind == 'cast']
    assert order[:2] == [0, 1], f'未知状态的技能点一次后应让位，实际 {order}'


def test_stops_on_stop_check():
    """stop_check 命中就退出"""
    host = FakeHost(make_plan(2))
    calls = {'n': 0}

    def stop():
        calls['n'] += 1
        return calls['n'] > 1

    host.battle_attack_loop(stop_check=stop, cooldown_timeout=0.05, poll_interval=0.0)
    assert host.events == [('cast', 0)], host.events


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
