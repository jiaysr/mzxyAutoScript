# -*- coding: utf-8 -*-
"""
世界任务「接 7 个跨服任务」单测（不连设备）

真机结论（2026-11-05）：
- 接过的任务会被游戏**移到列表末尾**，所以正常情况每次接第一行就能按顺序接完；
- 点「接取」会弹「当前任务加量功能未激活」，处理方式与 AutoTask.click_auto_start 一致：
  出现就点绿X关掉，**不勾「今日不再提示」**（那是玩家的游戏设置）。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_accept.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_accept.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.WorldTask.script_task import ScriptTask

ROW_TOP = 214
ROW_PITCH = 87


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


def make_task(names, use_bonus=False):
    """
    造一个「列表里正好有 names 这些任务」的任务实例

    点接取时模拟游戏行为：把该行移到列表末尾。
    """
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(use_bonus=use_bonus)))
    state = {'rows': [(f'【跨服】{name}', 1091, ROW_TOP + i * ROW_PITCH)
                      for i, name in enumerate(names)]}
    task.state = state
    task.task_rows = lambda: list(state['rows'])

    def click(x=None, y=None, control_name=None, **kwargs):
        task.device.clicks.append((x, y, control_name))
        for index, (_text, _bx, by) in enumerate(state['rows']):
            if abs(by - y) <= 2:
                state['rows'].append(state['rows'].pop(index))
                break

    task.device.click = click
    return task


# ---------------------------------------------------------------- 任务名匹配
def test_task_name_match_strips_prefix():
    task = ScriptTask.__new__(ScriptTask)
    # 列表里带【跨服】前缀，目标名不带
    assert task.task_name_match('【跨服】世界之巅', '世界之巅') is True
    assert task.task_name_match('[跨服]黄金比例', '黄金比例') is True
    assert task.task_name_match('【跨服-周六限时】炎黄大战傀儡', '炎黄大战傀儡') is True
    # OCR 漏字/错字也要能认
    assert task.task_name_match('【跨服】世界之颠', '世界之巅') is True
    assert task.task_name_match('【跨服】木料之', '木料之争') is True
    # 不能把别的任务认成目标
    assert task.task_name_match('【跨服】恶魔印记', '世界之巅') is False
    assert task.task_name_match('', '世界之巅') is False
    # 「世界之X」前三个字一样，相似度 0.75，绝不能互相认错
    assert task.task_name_match('【跨服】世界之心', '世界之巅') is False
    assert task.task_name_match('【跨服】世界之巅', '世界之心') is False
    assert task.task_name_match('【跨服】世界印记', '世界之心') is False


# ---------------------------------------------------------------- 接取顺序
def test_accept_all_in_order():
    """7 个任务按顺序接：每次接第一行，被接的会移到末尾"""
    names = ['世界之巅', '黄金比例', '木料之争', '兽毛之争', '世界印记', '为了荣耀', '世界之心']
    task = make_task(names)
    task.close_bonus_promo = lambda timeout=10: True
    task.wait_task_accepted = lambda name, timeout=8: True

    accepted = task.accept_tasks()
    assert accepted == names, accepted
    assert len(task.device.clicks) == 7, task.device.clicks
    # 点的都是当次列表第一行的接取按钮
    ys = [y for _x, y, _n in task.device.clicks]
    assert ys == [ROW_TOP + i * ROW_PITCH for i in range(7)], ys


def test_accept_skips_already_accepted():
    """今天已经接过（列表里看不到）的任务跳过，别卡住"""
    names = ['黄金比例', '木料之争', '兽毛之争', '世界印记', '为了荣耀']
    task = make_task(names)
    task.close_bonus_promo = lambda timeout=10: True
    task.wait_task_accepted = lambda name, timeout=8: True

    accepted = task.accept_tasks()
    assert accepted == names, accepted
    assert len(task.device.clicks) == len(names)


def test_accept_finds_row_by_name_when_order_is_odd():
    """第一行名字对不上时，按名字在可见行里找（OCR 兜底）"""
    names = ['恶魔印记', '世界印记', '为了荣耀']
    task = make_task(names)
    task.close_bonus_promo = lambda timeout=10: True
    task.wait_task_accepted = lambda name, timeout=8: True

    accepted = task.accept_tasks()
    # 世界之巅等 5 个不在列表里 -> 跳过；世界印记/为了荣耀按名字找到
    assert accepted == ['世界印记', '为了荣耀'], accepted


def test_accept_click_failure_does_not_crash():
    """接取后列表没变化：只告警，继续接下一个"""
    names = ['世界之巅', '黄金比例']
    task = make_task(names)
    task.close_bonus_promo = lambda timeout=10: True
    task.wait_task_accepted = lambda name, timeout=8: False

    accepted = task.accept_tasks()
    assert accepted == [], '没确认接上就不该算成功'
    assert len(task.device.clicks) == 2


def test_accept_handles_unreadable_list():
    task = make_task([])
    task.task_rows = lambda: []
    task.close_bonus_promo = lambda timeout=10: True
    task.wait_task_accepted = lambda name, timeout=8: True

    assert task.accept_tasks() == []
    assert task.device.clicks == []


# ---------------------------------------------------------------- 加量弹窗
def test_close_bonus_promo_closes_without_ticking():
    """加量弹窗：点绿X关掉即可，不能勾「今日不再提示」"""
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    clicks = []
    task.appear_then_click = lambda rule, interval=None: clicks.append(rule) or True

    states = [True, True, False]      # 弹窗 -> 点X -> 关掉
    task.bonus_promo_open = lambda: states.pop(0) if states else False

    assert task.close_bonus_promo(timeout=3) is True
    assert len(clicks) >= 1, '弹窗出现时应该点绿X'
    names = [x[2] for x in task.device.clicks]
    assert all('no_remind' not in (n or '') for n in names), f'不该点勾选框: {names}'


def test_close_bonus_promo_noop_when_absent():
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.bonus_promo_open = lambda: False
    assert task.close_bonus_promo(timeout=1) is True
    assert task.device.clicks == []


def main():
    cases = [
        test_task_name_match_strips_prefix,
        test_accept_all_in_order,
        test_accept_skips_already_accepted,
        test_accept_finds_row_by_name_when_order_is_odd,
        test_accept_click_failure_does_not_crash,
        test_accept_handles_unreadable_list,
        test_close_bonus_promo_closes_without_ticking,
        test_close_bonus_promo_noop_when_absent,
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
