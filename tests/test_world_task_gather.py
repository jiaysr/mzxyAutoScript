# -*- coding: utf-8 -*-
"""
世界任务「木料之争」单测（不连设备）

真机玩法（2026-11-05 确认）：
1. 任务页里点「木料之争」这一行的「寻路」；
2. 关掉任务页，等人物自己走到采集点 (145,470) 附近停下；
3. 切目标直到锁到一个「采集」动作的目标（**不校验名字**）→ 点右下角动作按钮（采集）
   → 按钮从「采集」变回「攻击」= 这一份采完 → 继续锁下一个；
4. 直到聊天栏报「【跨服】木料之争已完成」。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_gather.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_gather.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.slots import ACTION_BUTTON_DISC_CENTER
from tasks.WorldTask.script_task import ScriptTask

# 真机任务页 OCR 出来的布局（按 y 分行，任务名会被拆成多段）
TASK_PAGE_ITEMS = [
    ('[66]', 308, 170), ('【跨服】世界之巅', 461, 169), ('进行中', 957, 168), ('寻路', 1094, 169),
    ('[66]', 309, 248), ('【跨服】黄金比例', 460, 246), ('完成', 973, 246), ('寻路', 1094, 247),
    ('[66]', 308, 326), ('【跨服】木料之争', 461, 324), ('进行中', 958, 324), ('寻路', 1093, 324),
    # 这一行 OCR 把「兽毛之争」拆成了两段
    ('[66]', 308, 402), ('【跨服】兽', 405, 402), ('毛之争', 501, 402),
    ('进行中', 959, 401), ('寻路', 1093, 402),
]


class FakeDevice:
    image = None

    def __init__(self):
        self.clicks = []
        self.drags = []

    def click(self, x=None, y=None, control_name=None, **kwargs):
        self.clicks.append((x, y, control_name))

    def hold_drag(self, start=None, end=None, hold=0.2, control_name=None):
        self.drags.append((start, end))

    def click_record_clear(self):
        pass

    def sleep(self, seconds):
        pass

    def stuck_record_clear(self):
        pass


def make_task() -> ScriptTask:
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(
            location='域外灵岛', bonus_multiplier=None)))
    return task


# ---------------------------------------------------------------- 任务页
def test_task_page_rows_pairs_split_names():
    """任务名被 OCR 拆成多段时也能拼回来，状态和寻路按钮按同一行配对"""
    task = make_task()
    task.task_page_items = lambda: list(TASK_PAGE_ITEMS)

    rows = task.task_page_rows()
    assert [r[0] for r in rows] == ['【跨服】世界之巅', '【跨服】黄金比例',
                                    '【跨服】木料之争', '【跨服】兽毛之争'], rows
    assert [r[1] for r in rows] == ['进行中', '完成', '进行中', '进行中'], rows
    # 寻路按钮坐标来自 OCR
    assert [r[3] for r in rows] == [169, 247, 324, 402], rows


def test_find_task_page_row_and_click():
    task = make_task()
    task.task_page_items = lambda: list(TASK_PAGE_ITEMS)

    row = task.find_task_page_row('木料之争')
    assert row == ('【跨服】木料之争', '进行中', 1093, 324), row

    assert task.task_page_walk_to('木料之争') is True
    assert task.device.clicks == [(1093, 324, 'walk_木料之争')], task.device.clicks

    # 找不到的任务：不点
    assert task.task_page_walk_to('不存在的任务') is False
    assert len(task.device.clicks) == 1


# ---------------------------------------------------------------- 走到采集点
def test_map_wait_arrive_at_ok():
    """轮询到位就返回，不要求「站定几秒」"""
    task = make_task()
    positions = [('世界之巅', 100, 100), ('世界之巅', 145, 470)]
    task.map_move_read_pos = lambda retry=None: positions.pop(0) if positions else ('世界之巅', 145, 470)
    assert task.map_wait_arrive_at((145, 470), timeout=3) is True


def test_map_wait_arrive_at_timeout_when_never_arrives():
    task = make_task()
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 300, 300)
    assert task.map_wait_arrive_at((145, 470), timeout=0.3) is False


# ---------------------------------------------------------------- 采集循环
def test_gather_until_complete():
    """锁采集物 -> 点动作按钮 -> 等按钮变回攻击 -> 任务完成收工"""
    task = make_task()
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: True
    # 第一次没完成、第二次报完成
    flags = [False, False, True]
    task.task_completed = lambda name: flags.pop(0) if len(flags) > 1 else flags[0]
    task.ui_target_lost_check = lambda action=None: (lambda: True)

    assert task.gather_until_complete('木料之争', timeout=3) is True
    # 点的是右下角动作按钮
    assert task.device.clicks and all(c[:2] == ACTION_BUTTON_DISC_CENTER for c in task.device.clicks), \
        task.device.clicks


def test_gather_retries_when_no_collectible():
    """附近没采集物：只告警不崩，超时返回 False"""
    task = make_task()
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: False
    task.task_completed = lambda name: False
    task.ui_target_lost_check = lambda action=None: (lambda: True)

    assert task.gather_until_complete('木料之争', timeout=0.5) is False
    assert task.device.clicks == [], '没锁到目标就不该乱点动作按钮'


# ---------------------------------------------------------------- 任务编排
def test_gather_task_skips_when_task_page_says_done():
    """任务页状态已是「完成」：不寻路、不走位"""
    task = make_task()
    task.task_completed = lambda name: False
    task.open_task_page = lambda timeout=30: True
    task.find_task_page_row = lambda name: ('【跨服】世界之巅', '完成', 1094, 169)
    task.close_task_page = lambda timeout=10: True
    walked = []
    task.task_page_walk_to = lambda name: walked.append(name) or True
    task.map_wait_arrive_at = lambda position, **kwargs: True
    task.gather_until_complete = lambda name, timeout=600: True

    assert task.run_gather_task('世界之巅', (302, 250)) is True
    assert walked == [], '已完成的任务不该再点寻路'


def test_gather_task_skips_when_completed_this_run():
    """本次运行已收到完成提示：连任务页都不用开"""
    task = make_task()
    task.task_completed = lambda name: True
    opened = []
    task.open_task_page = lambda timeout=30: opened.append(1) or True

    assert task.run_gather_task('木料之争', (145, 470)) is True
    assert opened == []


def test_gather_task_full_flow():
    task = make_task()
    task.task_completed = lambda name: False
    task.open_task_page = lambda timeout=30: True
    task.find_task_page_row = lambda name, max_scroll=3: (f'【跨服】{name}', '进行中', 1093, 324)
    task.task_page_walk_to = lambda name, row=None: True
    task.wait_task_page_closed = lambda timeout=6: True
    closer = []
    task.close_task_page = lambda timeout=10: closer.append(1) or True
    task.map_wait_arrive_at = lambda position, **kwargs: True
    order = []
    task.gather_until_complete = lambda name, timeout=600: order.append(name) or True

    assert task.run_gather_task('木料之争', (145, 470)) is True
    assert order == ['木料之争']
    assert closer == [], '点完寻路任务页会自己关，不该再手动点关闭'


def test_gather_task_falls_back_to_close_when_page_stays():
    """点了寻路任务页却没关：兜底关掉，不继续往下走"""
    task = make_task()
    task.task_completed = lambda name: False
    task.open_task_page = lambda timeout=30: True
    task.find_task_page_row = lambda name, max_scroll=3: (f'【跨服】{name}', '进行中', 1093, 324)
    task.task_page_walk_to = lambda name, row=None: True
    task.wait_task_page_closed = lambda timeout=6: False
    arrived = []
    task.map_wait_arrive_at = lambda position, **kwargs: arrived.append(1) or True
    task.gather_until_complete = lambda name, timeout=600: True

    assert task.run_gather_task('木料之争', (145, 470)) is False
    assert arrived == []


def test_find_task_page_row_scrolls_when_list_is_long():
    """任务多时列表可滚动：当前屏没有就往下翻着找"""
    task = make_task()
    pages = [
        [('【跨服】木料之争', '进行中', 1093, 324)],
        [('【跨服】世界印记', '进行中', 1093, 324)],   # 翻一页后看到目标
    ]
    state = {'page': 0}
    task.task_page_rows = lambda: list(pages[state['page']])
    swipes = []

    def swipe(forward=True, before=None):
        swipes.append(forward)
        if state['page'] + 1 >= len(pages):
            return False
        state['page'] += 1
        return True

    task.swipe_task_page_list = swipe

    assert task.find_task_page_row('世界印记') == ('【跨服】世界印记', '进行中', 1093, 324)
    assert swipes == [True]


def test_find_task_page_row_stops_at_list_bottom():
    """翻到底还是没有：返回 None，不无限滑"""
    task = make_task()
    task.task_page_rows = lambda: [('【跨服】木料之争', '进行中', 1093, 324)]
    task.swipe_task_page_list = lambda forward=True, before=None: False

    assert task.find_task_page_row('世界印记') is None


def test_gather_task_table():
    """采集类任务表：三个任务的完成点（顺序无关，按表驱动执行）"""
    table = dict(ScriptTask.GATHER_TASKS)
    assert table == {'木料之争': (145, 470), '世界之巅': (302, 250), '世界印记': (428, 107)}, table


def main():
    cases = [
        test_task_page_rows_pairs_split_names,
        test_find_task_page_row_and_click,
        test_map_wait_arrive_at_ok,
        test_map_wait_arrive_at_timeout_when_never_arrives,
        test_gather_until_complete,
        test_gather_retries_when_no_collectible,
        test_gather_task_skips_when_task_page_says_done,
        test_gather_task_skips_when_completed_this_run,
        test_gather_task_full_flow,
        test_gather_task_falls_back_to_close_when_page_stays,
        test_find_task_page_row_scrolls_when_list_is_long,
        test_find_task_page_row_stops_at_list_bottom,
        test_gather_task_table,
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
