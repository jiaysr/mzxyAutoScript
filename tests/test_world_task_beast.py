# -*- coding: utf-8 -*-
"""
世界任务「兽毛之争」单测（不连设备）

真机玩法（2026-11-05 确认）：
1. 打开小地图、视口拖到**右下角**，点野怪刷新点（C_BEAST_POINT）；
2. 关掉小地图，等人物走到打架位 (226,375)；
3. 锁「野兽」并按任务「战斗配置」里的预设放技能；**打死 4~5 秒会刷新**，
   所以目标丢了直接重新锁，不用补锁逻辑；
4. 打的同时**每 5 秒查一次坐标**，偏离打架位就回点位再接着打，直到任务完成。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_beast.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_beast.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.base.timer import Timer
from tasks.WorldTask.script_task import ScriptTask


class FakeDevice:
    image = None

    def __init__(self):
        self.clicks = []

    def click(self, x=None, y=None, control_name=None, **kwargs):
        self.clicks.append((x, y, control_name))

    def click_record_clear(self):
        pass

    def sleep(self, seconds):
        pass

    def stuck_record_clear(self):
        pass


def make_task():
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(
            location='域外灵岛', bonus_multiplier=None)))
    return task


# ---------------------------------------------------------------- 坐标跑偏
def test_beast_drifted():
    task = make_task()
    readings = [('世界之巅', 226, 375), ('世界之巅', 240, 375), None]
    task.map_move_read_pos = lambda retry=None: readings.pop(0)

    assert task.beast_drifted() is False, '正好在打架位，没跑偏'
    assert task.beast_drifted() is True, '偏了 14 格，要回点位'
    assert task.beast_drifted() is False, '坐标读不出来按没跑偏处理'


# ---------------------------------------------------------------- 停止条件
def test_beast_stop_reason_complete():
    task = make_task()
    task.task_completed = lambda name: True
    assert task.beast_stop_reason() == 'complete'


def test_beast_stop_reason_drift():
    """每 5 秒才查一次坐标：计时没到不查，到了且跑偏就返回 drift"""
    task = make_task()
    task.task_completed = lambda name: False
    calls = []
    task.beast_drifted = lambda: calls.append(1) or True

    task.beast_drift_timer = Timer(60).start()      # 还没到 5 秒
    assert task.beast_stop_reason() is None
    assert calls == [], '计时没到不该去查坐标（查坐标要 OCR）'

    task.beast_drift_timer = Timer(-1).start()      # 立刻到点（0 秒在 Windows 上可能同毫秒不算到）
    assert task.beast_stop_reason() == 'drift'
    assert calls == [1]


# ---------------------------------------------------------------- 去点位
def test_beast_goto_fight_point():
    task = make_task()
    events = []
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 300, 500)   # 人还在远处
    task.map_open_minimap_tap = lambda timeout=8: events.append('open') or True
    task.map_pan_to_corner = lambda corner, **kwargs: events.append(f'pan:{corner}') or True
    task.close_minimap_if_open = lambda timeout=3: events.append('close') or True
    task.map_wait_arrive_at = lambda position, **kwargs: events.append(f'arrive:{position}') or True

    assert task.beast_goto_fight_point() is True
    assert events == ['open', 'pan:bottom_right', 'close', 'arrive:(226, 375)'], events
    clicked = task.device.clicks[0]
    assert clicked[:2] == (455, 407), f'小地图点位要精确点到坐标上，实际 {clicked[:2]}'
    assert clicked[2] == 'beast_point'


def test_beast_goto_fight_point_skips_when_already_near():
    """被打退几步、人还在打架位附近：不重开地图（重开一次要 5s+）"""
    task = make_task()
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 230, 378)   # 容差内
    opened = []
    task.map_open_minimap_tap = lambda timeout=8: opened.append(1) or True
    task.map_wait_arrive_at = lambda position, **kwargs: True

    assert task.beast_goto_fight_point() is True
    assert opened == [], '在点位附近就不该再开地图'
    assert task.device.clicks == []


def test_beast_goto_fight_point_pan_fails():
    task = make_task()
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 300, 500)
    task.map_open_minimap_tap = lambda timeout=8: True
    task.map_pan_to_corner = lambda corner, **kwargs: False
    task.map_wait_arrive_at = lambda position, **kwargs: True

    assert task.beast_goto_fight_point() is False
    assert task.device.clicks == [], '地图没拖到位就不该点刷新点'


# ---------------------------------------------------------------- 打怪循环
def test_run_beast_task_skips_when_completed():
    task = make_task()
    task.task_completed = lambda name: True
    called = []
    task.beast_goto_fight_point = lambda: called.append('goto') or True

    assert task.run_beast_task() is True
    assert called == []


def test_beast_fight_returns_when_completed():
    task = make_task()
    flags = [False, True]        # 进循环时没完成，打完一轮报完成
    task.task_completed = lambda name: flags.pop(0) if len(flags) > 1 else flags[0]
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0, 1]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: True
    reasons = ['target_lost']
    task.battle_skill_loop = lambda slots, **kwargs: reasons.pop(0) if reasons else 'timeout'

    assert task.beast_fight_until_complete() is True


def test_beast_fight_regotos_on_drift():
    """打的过程中跑偏：回一次点位再接着打"""
    task = make_task()
    task.task_completed = lambda name: False
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: True
    regotos = []
    task.beast_goto_fight_point = lambda: regotos.append(1) or True

    reasons = ['drift', 'target_lost', 'complete']
    def loop(slots, **kwargs):
        reason = reasons.pop(0)
        if reason == 'complete':
            task.task_completed = lambda name: True
            return 'target_lost'
        return reason
    task.battle_skill_loop = loop

    assert task.beast_fight_until_complete() is True
    assert regotos == [1], 'drift 之后应该回一次点位'


def test_beast_fight_gives_up_when_cannot_relock_position():
    """没法重新锁到目标（附近没怪）：一直等到超时就返回 False，不崩"""
    task = make_task()
    task.task_completed = lambda name: False
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: False
    task.BEAST_FIGHT_TIMEOUT = 0.3

    assert task.beast_fight_until_complete() is False


def main():
    cases = [
        test_beast_drifted,
        test_beast_stop_reason_complete,
        test_beast_stop_reason_drift,
        test_beast_goto_fight_point,
        test_beast_goto_fight_point_skips_when_already_near,
        test_beast_goto_fight_point_pan_fails,
        test_run_beast_task_skips_when_completed,
        test_beast_fight_returns_when_completed,
        test_beast_fight_regotos_on_drift,
        test_beast_fight_gives_up_when_cannot_relock_position,
    ]
    failed = 0
    for case in cases:
        try:
            case()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {case.__name__}: {e}')
        else:
            print(f'PASS {case.__name__}')
    if failed:
        print(f'\n{failed} / {len(cases)} failed')
        sys.exit(1)
    print(f'\nall {len(cases)} passed')


if __name__ == '__main__':
    main()
