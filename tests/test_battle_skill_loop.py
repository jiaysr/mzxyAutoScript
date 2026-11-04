# -*- coding: utf-8 -*-
"""
battle_skill_loop() 单测（不连设备）

队列模式是项目唯一的战斗模式，这里固化它的关键行为：
- 每轮挑「优先级最高、冷却好」的技能释放，放完再挑下一个
- 高优先级技能冷却好了会插队
- 连点次数 / 点击间隔 / 重试次数 / 冷却等待上限都从「战斗预设」取
- 显式传参可以覆盖预设（调试用）
- 全在冷却时只补普通攻击
- check_stop 返回非空就退出

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_skill_loop.py
"""
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.attack import BattleAttack
from tasks.Battle.config import Battle
from tasks.Battle.cooldown import SKILL_COOLING, SKILL_READY
from tasks.Battle.planner import PlanEntry


class FakeDevice:
    def __init__(self) -> None:
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(round(float(seconds), 3))
        # 冷却轮询在这里被 fake 掉了，真睡只会拖慢测试
        time.sleep(0)


class FakeHost(BattleAttack):
    """只实现 battle_skill_loop 需要的东西（点击与冷切都用假的）"""

    def __init__(self, plan, never_cool=False):
        self.config = SimpleNamespace(battle=Battle())
        self.device = FakeDevice()
        self.plan = list(plan)
        self.slots = [entry.slot for entry in self.plan]
        self.never_cool = never_cool
        self.cooling = set()
        self.clicks = []          # (slot, clicks, interval)
        self.attacks = 0
        self.shots = 0
        self.casts = 0            # 成功释放（进入冷却）的技能数
        self.wait_timeouts = []   # 每次等冷却传入的 timeout

    # ---- 画面 / 状态
    def screenshot(self):
        self.shots += 1
        return None

    def battle_plan(self, preset=None):
        return self.plan

    def battle_skills_state(self, image=None):
        return {slot: self.state_of(slot) for slot in self.slots}

    def battle_skill_state(self, slot, image=None):
        return self.state_of(slot)

    def state_of(self, slot):
        return SKILL_COOLING if slot in self.cooling else SKILL_READY

    def battle_use_normal_attack(self):
        return True

    # ---- 点击 / 等冷却
    def battle_click_skill_multi(self, slot, clicks=None, interval=None):
        self.clicks.append((slot, clicks, interval))

    def battle_click_attack(self):
        self.attacks += 1

    def battle_wait_cooldown_checked(self, slot, target_check=None,
                                     timeout=2.0, poll_interval=0.1):
        self.wait_timeouts.append(timeout)
        if self.never_cool:
            return 'failed'
        self.cooling.add(slot)
        return 'cooled'


def make_plan(count=3):
    return [PlanEntry(slot=i, skill_id=f's{i}', name=f'技能{i + 1}', priority=i + 1)
            for i in range(count)]


def run_loop(host, casts=None, max_calls=200, **kwargs):
    """跑到成功释放 [casts] 次（或 check_stop 调用太多次）就停"""
    state = {'calls': 0}

    def stop(image=None):
        state['calls'] += 1
        if casts is not None and host.casts >= casts:
            return 'stop'
        if state['calls'] > max_calls:
            return 'stop'
        return None

    def on_cast(count):
        host.casts = count

    options = dict(check_stop=stop, on_cast=on_cast, timeout=None, attack_interval=0.0)
    options.update(kwargs)
    return host.battle_skill_loop(host.slots, **options)


# --------------------------------------------------------------------- 用例
def test_priority_order():
    """按优先级依次放：技能1 -> 技能2 -> 技能3"""
    host = FakeHost(make_plan(3))
    run_loop(host, casts=3)
    order = [slot for slot, _, _ in host.clicks]
    assert order == [0, 1, 2], f'应按优先级 0->1->2，实际 {order}'


def test_insert_higher_priority():
    """高优先级技能冷却好了要插队：放完 2 号技能时 1 号冷却结束，下一个应该是 1 号"""
    host = FakeHost(make_plan(3))
    original = host.battle_wait_cooldown_checked

    def wait_then_free_high(slot, target_check=None, timeout=2.0, poll_interval=0.1):
        result = original(slot, target_check=target_check, timeout=timeout,
                          poll_interval=poll_interval)
        if slot == 1:
            host.cooling.discard(0)
        return result

    host.battle_wait_cooldown_checked = wait_then_free_high
    run_loop(host, casts=3)
    order = [slot for slot, _, _ in host.clicks]
    assert order == [0, 1, 0], f'应该插队放技能1，实际 {order}'


def set_common(host, **kwargs):
    """改这个假宿主读到的「战斗设置」（battle.common，全局共享）"""
    common = host.config.battle.common
    for key, value in kwargs.items():
        setattr(common, key, value)


def test_release_params_from_settings():
    """连点次数 / 点击间隔 / 冷却超时都取「战斗设置」里的值"""
    host = FakeHost(make_plan(1))
    set_common(host, clicks=3, click_interval=0.3, attempts=2, cooldown_timeout=0.6)
    run_loop(host, casts=1)
    assert host.clicks[0] == (0, 3, 0.3), host.clicks[0]
    assert host.wait_timeouts[0] == 0.6, host.wait_timeouts


def test_attempts_from_settings():
    """点了也不进冷却 -> 单次释放内按 attempts 重试，不能死磕"""
    host = FakeHost(make_plan(2), never_cool=True)
    set_common(host, clicks=2, click_interval=0.1, attempts=2, cooldown_timeout=0.2)
    run_loop(host, max_calls=3)
    first_round = host.clicks[:2]       # 第一次 release：attempts=2 -> 点两组
    assert [c[0] for c in first_round] == [0, 0], first_round
    assert all(c[1] == 2 for c in first_round), first_round
    assert host.wait_timeouts[:2] == [0.2, 0.2], host.wait_timeouts

    # 对照：attempts=1 时同一次释放只点一组，随后让位给下一个技能
    host2 = FakeHost(make_plan(2), never_cool=True)
    set_common(host2, clicks=2, click_interval=0.1, attempts=1, cooldown_timeout=0.2)
    run_loop(host2, max_calls=3)
    assert [c[0] for c in host2.clicks[:2]] == [0, 1], host2.clicks[:2]


def test_explicit_params_override_settings():
    """显式传参优先于战斗设置（调试单点用）"""
    host = FakeHost(make_plan(2), never_cool=True)
    set_common(host, clicks=4, click_interval=0.5, attempts=3, cooldown_timeout=2.0)
    run_loop(host, clicks=1, click_interval=0.05,
             max_attempts=1, cooldown_timeout=0.2, max_calls=4)
    assert host.clicks[0] == (0, 1, 0.05), host.clicks[0]
    assert host.wait_timeouts[0] == 0.2, host.wait_timeouts


def test_default_settings_params():
    """默认战斗设置：连点 2 次 / 间隔 0.2 / 重试 3 次 / 冷却超时 1.2"""
    host = FakeHost(make_plan(1))
    release = host.battle_release_config()
    assert release == {'clicks': 2, 'click_interval': 0.2,
                       'attempts': 3, 'cooldown_timeout': 1.2}, release


def test_attacks_when_all_cooling():
    """全在冷却：只补普通攻击，不点任何技能"""
    host = FakeHost(make_plan(3))
    host.cooling = {entry.slot for entry in host.plan}
    run_loop(host, casts=None, max_calls=5)
    assert host.clicks == [], f'冷却中不该点技能: {host.clicks}'
    assert host.attacks >= 1, '没技能可放时应该补普通攻击'


def test_stops_on_stop_check():
    """check_stop 返回非空就退出"""
    host = FakeHost(make_plan(2))

    def stop(image=None):
        return 'reward'

    result = host.battle_skill_loop(host.slots, check_stop=stop, timeout=None)
    assert result == 'reward', result
    assert host.clicks == [], '停止条件命中后不应该再点技能'


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
