# -*- coding: utf-8 -*-
"""
技能位身份缓存单测（不连设备）

规则（见 tasks/Battle/attack.py: battle_slot_matches）：

- 一次任务里技能栏不会变，**认出来就记住**，之后只判冷却，不再做模板匹配；
- 还没认出来的位，只在「当前不冷却 + 这个状态还没试过」时补认一次：
  - 冷却中不认（遮罩会让图标失真），冷却结束后自动重认一次；
  - 冷却好还认不出来 = 这一位不是目录里的技能（物品/新技能），不反复认；
- 报出来的释放顺序也一起缓存，技能位身份变化（认出新位 / 手动清理）才重算。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_slot_cache.py
装了 pytest 时也可以：python -m pytest tests/test_battle_slot_cache.py
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
from tasks.Battle.cooldown import SKILL_COOLING, SKILL_READY
from tasks.Battle.presets import default_preset
from tasks.Battle.recognizer import SlotMatch

IMAGE = object()


class CountingHost(BattleAttack):
    """记录「整排识别」和「单点补认」各发生了几次"""

    def __init__(self, matches, states, identify_results=None):
        self.config = SimpleNamespace(battle=Battle())
        self.device = SimpleNamespace(image=IMAGE)
        self.full_matches = dict(matches)
        self.states = dict(states)
        self.identify_results = dict(identify_results or {})
        self.full_calls = 0          # battle_slot_skills 调用次数（整排）
        self.identify_calls = 0      # 单点补认次数
        self.identify_slots = []

    def screenshot(self):
        return IMAGE

    def exist_image(self):
        return True

    def _battle_image(self, image=None):
        return image if image is not None else IMAGE

    def battle_slot_skills(self, image=None):
        self.full_calls += 1
        return self.full_matches

    def battle_states_raw(self, image=None):
        return self.states

    def battle_recognizer(self):
        host = self

        class Recognizer:
            def identify(self, image, regions):
                host.identify_calls += 1
                host.identify_slots.extend(sorted(regions))
                return {slot: host.identify_results[slot]
                        for slot in regions if slot in host.identify_results}

        return Recognizer()


class NoCacheHost(CountingHost):
    """老行为：每次计划都整排重认"""

    BATTLE_SLOT_CACHE = False


def recognized(slot, skill_id, score=0.99):
    return SlotMatch(slot, skill_id, score)


def unknown(slot, score=0.61):
    return SlotMatch(slot, None, score)


def plan_slots(host):
    return [entry.slot for entry in host.battle_plan(preset=default_preset())]


# --------------------------------------------------------------------- 用例
def test_slots_are_recognized_only_once():
    """核心：连着做两次计划，第二次不再整排识别，结果还一样"""
    host = CountingHost({0: recognized(0, 'jiyu_1'), 1: recognized(1, 'jiyu_2')},
                        {0: SKILL_READY, 1: SKILL_READY})
    first = plan_slots(host)
    assert host.full_calls == 1, host.full_calls

    second = plan_slots(host)
    assert host.full_calls == 1, '第二次计划不该再整排识别（技能位不会变）'
    assert host.identify_calls == 0, '全都认出来了，不该有单点补认'
    assert second == first == [0, 1], (first, second)


def test_release_order_is_cached_too():
    """释放顺序也一起缓存：第二次连 build_plan 都不用重算（计划对象直接复用）"""
    host = CountingHost({0: recognized(0, 'jiyu_1'), 1: recognized(1, 'jiyu_2')},
                        {0: SKILL_READY, 1: SKILL_READY})
    first = host.battle_plan(preset=default_preset())
    second = host.battle_plan(preset=default_preset())
    assert [e.slot for e in first] == [e.slot for e in second]
    assert first == second, '缓存命中的计划应与首次一致'
    assert host.full_calls == 1


def test_cooling_slot_is_not_recognized_until_ready():
    """冷却中的位不补认（遮罩下认了也不可信），冷却好之后自动重认一次并插回队列"""
    host = CountingHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                        {0: SKILL_READY, 1: SKILL_COOLING},
                        identify_results={1: recognized(1, 'jiyu_2')})
    assert plan_slots(host) == [0]
    assert host.identify_calls == 0, '冷却中不该补认'

    host.states = {0: SKILL_READY, 1: SKILL_READY}
    assert plan_slots(host) == [0, 1], '冷却结束后应重认并插回队列'
    assert host.identify_calls == 1, '只该补认一次'
    assert host.identify_slots == [1], host.identify_slots


def test_ready_unknown_slot_is_not_retried():
    """
    冷却好还认不出来 = 这一位不是目录里的技能（物品 / 新技能）

    这种位每帧重认一遍没有意义（分数不会变），只白花 CPU —— 认一次就放弃这一轮，
    等它冷却过一次（状态变了）再给一次机会。
    """
    host = CountingHost({0: recognized(0, 'jiyu_1'), 1: unknown(1, score=0.5)},
                        {0: SKILL_READY, 1: SKILL_READY})
    host.battle_slot_matches()
    host.battle_slot_matches()
    host.battle_slot_matches()
    assert host.identify_calls == 0, f'不该反复补认: {host.identify_slots}'


def test_retry_slots_forces_reidentification():
    """冷却结束重认走 retry_slots：绕过「试过」限制强制再认一次"""
    host = CountingHost({0: unknown(0)}, {0: SKILL_COOLING},
                        identify_results={0: recognized(0, 'jiyu_1')})
    host.battle_slot_matches()
    assert host.identify_calls == 0
    host.states = {0: SKILL_READY}
    host.battle_slot_matches(retry_slots=(0,))
    assert host.identify_calls == 1
    assert host.battle_slot_version() >= 1, '认出新位要推进版本号（顺序缓存据此失效）'


def test_cache_clear_reidentifies():
    """换职业 / 切技能后调 battle_slot_cache_clear()，下个计划整排重认"""
    host = CountingHost({0: recognized(0, 'jiyu_1')}, {0: SKILL_READY})
    plan_slots(host)
    assert host.full_calls == 1

    host.battle_slot_cache_clear()
    plan_slots(host)
    assert host.full_calls == 2, '清掉缓存后应该重新整排识别'


def test_cache_clear_single_slot():
    """只清某一位：其它位身份保留（只补认这一位）"""
    host = CountingHost({0: recognized(0, 'jiyu_1'), 1: recognized(1, 'jiyu_2')},
                        {0: SKILL_READY, 1: SKILL_READY},
                        identify_results={1: recognized(1, 'jiyu_2')})
    plan_slots(host)
    host.battle_slot_cache_clear(1)
    plan_slots(host)
    assert host.full_calls == 1, '不该整排重认'
    assert host.identify_slots == [1], host.identify_slots


def test_new_slot_invalidates_order_cache():
    """认出一个新技能位后，缓存的释放顺序要失效重算（否则它永远排在最后）"""
    host = CountingHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                        {0: SKILL_READY, 1: SKILL_COOLING},
                        identify_results={1: recognized(1, 'jiyu_2')})
    plan_slots(host)
    version_before = host.battle_slot_version()

    host.states = {0: SKILL_READY, 1: SKILL_READY}
    assert plan_slots(host) == [0, 1]
    assert host.battle_slot_version() > version_before

    calls = host.identify_calls
    plan_slots(host)
    assert host.identify_calls == calls, '识别完就该全命中缓存'
    assert host.full_calls == 1, '整排识别只发生在第一次'


def test_cache_disabled_keeps_old_behaviour():
    """BATTLE_SLOT_CACHE=False：回到「每次计划整排重认」的老行为（回退开关）"""
    host = NoCacheHost({0: recognized(0, 'jiyu_1')}, {0: SKILL_READY})
    plan_slots(host)
    plan_slots(host)
    assert host.full_calls == 2, f'关掉缓存后每次都要重认: {host.full_calls}'


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
