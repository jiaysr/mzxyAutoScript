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
from tasks.AutoTask.config import BonusMultiplier

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


def make_task(names, bonus=BonusMultiplier.NONE):
    """
    造一个「列表里正好有 names 这些任务」的任务实例

    点接取时模拟游戏行为：把该行移到列表末尾。
    """
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(bonus_multiplier=bonus)))
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
    task.bonus_promo_open = lambda refresh=True: states.pop(0) if states else False

    assert task.close_bonus_promo(timeout=3) is True
    assert len(clicks) >= 1, '弹窗出现时应该点绿X'
    names = [x[2] for x in task.device.clicks]
    assert all('no_remind' not in (n or '') for n in names), f'不该点勾选框: {names}'


def test_close_bonus_promo_noop_when_absent():
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.bonus_promo_open = lambda refresh=True: False
    assert task.close_bonus_promo(timeout=1) is True
    assert task.device.clicks == []


def test_all_tasks_completed():
    """监测的结束条件：接取到的任务全部识别到「已完成」"""
    task = ScriptTask.__new__(ScriptTask)
    task.accepted_tasks = ['世界之巅', '黄金比例']
    task.completed_tasks = []
    assert task.all_tasks_completed() is False

    task.completed_tasks = ['【跨服】世界之巅']
    assert task.all_tasks_completed() is False, '还有一个没完成'

    task.completed_tasks = ['【跨服】世界之巅', '【跨服】黄金比例']
    assert task.all_tasks_completed() is True

    # 没接过任务时不算「全部完成」（否则刚开监测就停）
    task.accepted_tasks = []
    assert task.all_tasks_completed() is False


# ---------------------------------------------------------------- 加量卡
def make_bonus_task(bonus=BonusMultiplier.X3, counts=None, goto=True):
    """
    造一个测加量卡的实例（加量页的页面操作全部打成替身，不连设备）

    :param counts: read_bonus_counts() 的返回值，None 表示读不出来
    """
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(bonus_multiplier=bonus)))
    task.reset_records = lambda: None
    navigated = []
    task.ui_reset_current_page = lambda: navigated.append('reset')
    task.ui_goto = lambda page, timeout=None: navigated.append(page) or goto
    task.read_bonus_counts = lambda: counts if counts is not None else {'3x': None, '5x': None}
    states = []
    task.set_bonus_state = lambda want: states.append(want)
    task.navigated = navigated
    task.states = states
    return task


def test_enable_bonus_skipped_when_none():
    """配置成 none：根本不去加量页"""
    task = make_bonus_task(bonus=BonusMultiplier.NONE)
    assert task.enable_bonus() is False
    assert task.navigated == []
    assert task.states == []


def test_enable_bonus_skipped_when_cards_insufficient():
    """卡不足 7 张：跳过加量（不报错、也不激活）"""
    task = make_bonus_task(bonus=BonusMultiplier.X3, counts={'3x': 6, '5x': 99})
    assert task.enable_bonus() is False
    assert task.states == [], '卡不够就不该动加量状态'


def test_enable_bonus_activates_when_enough():
    """卡够 7 张：进加量页并启用对应那张"""
    task = make_bonus_task(bonus=BonusMultiplier.X3, counts={'3x': 7, '5x': 0})
    assert task.enable_bonus() is True
    assert task.states == [BonusMultiplier.X3]

    # 5 倍卡走另一侧
    task = make_bonus_task(bonus=BonusMultiplier.X5, counts={'3x': 0, '5x': 9})
    assert task.enable_bonus() is True
    assert task.states == [BonusMultiplier.X5]


def test_enable_bonus_when_count_unreadable():
    """数量读不出来：按不足处理，跳过加量"""
    task = make_bonus_task(bonus=BonusMultiplier.X3)
    assert task.enable_bonus() is False
    assert task.states == []


def test_disable_bonus_stops_both_sides():
    task = make_bonus_task(bonus=BonusMultiplier.X3, counts={'3x': 7, '5x': 0})
    assert task.disable_bonus() is True
    assert task.states == [BonusMultiplier.NONE]


def test_disable_bonus_failure_is_swallowed():
    """关加量页失败：只返回 False，不能把任务本身的成败覆盖掉"""
    task = make_bonus_task(bonus=BonusMultiplier.X3, goto=False)
    assert task.disable_bonus() is False
    assert task.states == []


# ---------------------------------------------------------------- 进入世界之巅
def make_enter_task(in_map, ensure_tab=True):
    """
    造一个测「进入世界之巅」的实例

    :param in_map: map_in_location() 的返回值序列（最后一个会一直重复）
    """
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.device.stuck_record_clear = lambda: None
    task.ui_reset_current_page = lambda: None
    task.reset_records = lambda: None
    states = {'in_map': list(in_map)}
    task.map_in_location = lambda name: (states['in_map'].pop(0) if len(states['in_map']) > 1
                                         else states['in_map'][0])
    task.ensure_activity_tab = lambda name=None, max_swipe=3: ensure_tab
    task.states = states
    return task


def test_enter_world_peak_when_already_inside():
    """已经在地图里：不点按钮"""
    task = make_enter_task([True])
    assert task.enter_world_peak(timeout=1) is True
    assert task.device.clicks == []


def test_enter_world_peak_clicks_and_waits():
    """不在图里：点按钮 -> 等地点变成世界之巅"""
    task = make_enter_task([False, True])
    assert task.enter_world_peak(timeout=2) is True
    assert len(task.device.clicks) == 1
    assert task.device.clicks[0][2] == 'enter_world_peak'


def test_enter_world_peak_retries_then_fails():
    """点两次都进不去：返回 False，并且点了两次"""
    task = make_enter_task([False])
    assert task.enter_world_peak(timeout=0.2) is False
    assert len(task.device.clicks) == 2


def test_enter_world_peak_needs_home_tab():
    """回不到世界之巅 tab 就不该瞎点"""
    task = make_enter_task([False], ensure_tab=False)
    assert task.enter_world_peak(timeout=1) is False
    assert task.device.clicks == []


# ---------------------------------------------------------------- 重启后重新进图
def test_ensure_world_peak_map_when_inside():
    """已经在地图里：不找 NPC、不开面板"""
    task = make_enter_task([True])
    events = []
    task.find_npc = lambda npc, retry=2: events.append('npc') or True
    task.open_activity_panel = lambda timeout=12: events.append('panel') or True

    assert task.ensure_world_peak_map() is True
    assert events == []


def test_ensure_world_peak_map_reenters_after_restart():
    """重启上线后人物会被送回域外灵岛：重新走 找NPC -> 开面板 -> 进图"""
    task = make_enter_task([False])
    events = []
    task.find_npc = lambda npc, retry=2: events.append('npc') or True
    task.open_activity_panel = lambda timeout=12: events.append('panel') or True
    task.ensure_activity_tab = lambda name=None, max_swipe=3: True
    task.detect_camp = lambda: None          # 进图后会顺手判断阵营，这里不关心
    flags = [False, False]
    task.map_in_location = lambda name: flags.pop(0) if flags else True

    assert task.ensure_world_peak_map(timeout=2) is True
    assert events == ['npc', 'panel'], events
    assert len(task.device.clicks) == 1, task.device.clicks


def test_ensure_world_peak_map_fails_without_npc():
    task = make_enter_task([False])
    task.find_npc = lambda npc, retry=2: False
    task.open_activity_panel = lambda timeout=12: True

    assert task.ensure_world_peak_map(timeout=1) is False
    assert task.device.clicks == []


def test_ensure_world_peak_map_fails_without_panel():
    task = make_enter_task([False])
    task.find_npc = lambda npc, retry=2: True
    task.open_activity_panel = lambda timeout=12: False

    assert task.ensure_world_peak_map(timeout=1) is False
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
        test_all_tasks_completed,
        test_enable_bonus_skipped_when_none,
        test_enable_bonus_skipped_when_cards_insufficient,
        test_enable_bonus_activates_when_enough,
        test_enable_bonus_when_count_unreadable,
        test_disable_bonus_stops_both_sides,
        test_disable_bonus_failure_is_swallowed,
        test_enter_world_peak_when_already_inside,
        test_enter_world_peak_clicks_and_waits,
        test_enter_world_peak_retries_then_fails,
        test_enter_world_peak_needs_home_tab,
        test_ensure_world_peak_map_when_inside,
        test_ensure_world_peak_map_reenters_after_restart,
        test_ensure_world_peak_map_fails_without_npc,
        test_ensure_world_peak_map_fails_without_panel,
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
