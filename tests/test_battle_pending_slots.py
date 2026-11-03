# -*- coding: utf-8 -*-
"""
「未识别 + 冷却中」技能位延迟入队单测（不连设备）

规则：
- 识别时未识别、且该位正在冷却 -> 先不入队，记为 pending；
- 冷却结束后重认一次：认出来就按预设顺序/禁用决定是否入队，仍认不出来按
  click_unknown_slot / unknown_slot_min_score 处理。

直接用 toolkit python 跑：
    toolkit\\python.exe tests\\test_battle_pending_slots.py
装了 pytest 时也可以：python -m pytest tests/test_battle_pending_slots.py
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


class FakeHost(BattleAttack):
    """只实现计划/重认需要的东西"""

    def __init__(self, matches, states, recheck_matches=None):
        self.config = SimpleNamespace(battle=Battle())
        self.device = SimpleNamespace(image=IMAGE)
        self.matches = dict(matches)
        self.states = dict(states)
        self.recheck_matches = dict(recheck_matches or {})

    def screenshot(self):
        return IMAGE

    def exist_image(self):
        return True

    def _battle_image(self, image=None):
        return image if image is not None else IMAGE

    def battle_slot_skills(self, image=None):
        return self.matches

    def battle_states_raw(self, image=None):
        return self.states

    def battle_recognizer(self):
        host = self

        class Recognizer:
            def identify(self, image, regions):
                return {slot: host.recheck_matches[slot]
                        for slot in regions if slot in host.recheck_matches}

        return Recognizer()


def recognized(slot, skill_id, score=0.99):
    return SlotMatch(slot, skill_id, score)


def unknown(slot, score=0.61):
    return SlotMatch(slot, None, score)


def plan_slots(host):
    return [entry.slot for entry in host.battle_plan(preset=default_preset())]


# --------------------------------------------------------------------- 用例
def test_unknown_cooling_slot_is_not_queued():
    """未识别 + 冷却中：先不入队，并记为 pending"""
    host = FakeHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                    {0: SKILL_READY, 1: SKILL_COOLING})
    assert plan_slots(host) == [0]
    assert host.battle_has_pending_slots()


def test_unknown_ready_slot_still_queued():
    """未识别但冷却好：维持原行为，按 unknown 规则（0.61 >= 0.6）排最后"""
    host = FakeHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                    {0: SKILL_READY, 1: SKILL_READY})
    assert plan_slots(host) == [0, 1]
    assert not host.battle_has_pending_slots()


def test_recheck_adds_slot_after_cooldown():
    """冷却结束重认出来：按预设优先级插回队列"""
    host = FakeHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                    {0: SKILL_READY, 1: SKILL_COOLING})
    plan_slots(host)

    host.states = {0: SKILL_READY, 1: SKILL_READY}
    host.recheck_matches = {1: recognized(1, 'jiyu_2')}
    updated = host.battle_recheck_pending(image=IMAGE)

    assert [entry.slot for entry in updated] == [0, 1], updated
    assert not host.battle_has_pending_slots()
    assert host.battle_current_slots() == [0, 1]


def test_recheck_respects_disabled_skill():
    """冷却结束重认出来是预设禁用的技能：不入队"""
    preset = default_preset()
    preset['disabled'] = ['jiyu_2']
    host = FakeHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                    {0: SKILL_READY, 1: SKILL_COOLING})

    plan = host.battle_plan(preset=preset)
    assert [entry.slot for entry in plan] == [0]

    host.states = {0: SKILL_READY, 1: SKILL_READY}
    host.recheck_matches = {1: recognized(1, 'jiyu_2')}
    updated = host.battle_recheck_pending(image=IMAGE)

    assert [entry.slot for entry in updated] == [0], updated
    assert not host.battle_has_pending_slots()


def test_low_score_slot_is_recovered_after_cooldown():
    """冷却中相似度低于 0.6 也不算物品：冷却结束重认后能正常入队"""
    host = FakeHost({0: recognized(0, 'jiyu_1'), 1: unknown(1, score=0.50)},
                    {0: SKILL_READY, 1: SKILL_COOLING})
    assert plan_slots(host) == [0]
    assert host.battle_has_pending_slots()

    host.states = {0: SKILL_READY, 1: SKILL_READY}
    host.recheck_matches = {1: recognized(1, 'jiyu_2')}
    updated = host.battle_recheck_pending(image=IMAGE)

    assert [entry.slot for entry in updated] == [0, 1], updated


def test_recheck_waits_while_still_cooling():
    """还在冷却：不重认、不改队列"""
    host = FakeHost({0: recognized(0, 'jiyu_1'), 1: unknown(1)},
                    {0: SKILL_READY, 1: SKILL_COOLING})
    plan_slots(host)
    assert host.battle_recheck_pending(image=IMAGE) is None
    assert host.battle_has_pending_slots()


if __name__ == '__main__':
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith('test_') and callable(obj)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f'[FAIL] {name}: {type(exc).__name__}: {exc}')
            traceback.print_exc()
        else:
            print(f'[PASS] {name}')
    print(f'共 {len(tests)} 个测试，失败 {failed} 个')
    raise SystemExit(1 if failed else 0)
