# -*- coding: utf-8 -*-
"""
世界任务「执行顺序」单测（不连设备）

真机约定（2026-11-05）：
- 固定顺序：兽毛之争 -> 木料之争 -> 为了荣耀 -> 世界之巅 -> 黄金比例 -> 世界印记 -> 世界之心；
- 任务页状态「完成」的跳过；
- **世界之心没有自己的玩法**：它是前面打怪时概率掉落的道具，所以排最后，
  还没完成就重打「为了荣耀」等掉落（最多 DROP_RETRY 次，掉落看脸，重试完还不掉就今天收工）。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_order.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_order.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.WorldTask.script_task import ScriptTask


class FakeDevice:
    image = None

    def sleep(self, seconds):
        pass

    def click_record_clear(self):
        pass


def make_task():
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(
            location='域外灵岛', bonus_multiplier=None)))
    return task


def test_world_peak_order_table():
    """顺序和玩法类型都按约定的来"""
    order = list(ScriptTask.WORLD_PEAK_ORDER)
    assert [name for name, _kind in order] == [
        '兽毛之争', '木料之争', '为了荣耀', '世界之巅', '黄金比例', '世界印记', '世界之心']
    assert dict(order) == {
        '兽毛之争': 'fight', '木料之争': 'gather', '为了荣耀': 'glory',
        '世界之巅': 'gather', '黄金比例': 'fight', '世界印记': 'gather',
        '世界之心': 'drop'}
    assert ScriptTask.DROP_TASK == '世界之心'


def test_run_world_peak_tasks_order():
    """按表顺序逐个做；世界之心（drop）不在循环里做，最后统一判断"""
    task = make_task()
    task.read_task_page_statuses = lambda names: [(n, 'pending') for n in names]
    task.task_completed = lambda name: False
    calls = []
    task.run_world_peak_task = lambda name, kind: calls.append(name) or True
    task.ensure_drop_task = lambda: calls.append('drop') or True

    assert task.run_world_peak_tasks() is True
    assert calls == ['兽毛之争', '木料之争', '为了荣耀', '世界之巅', '黄金比例', '世界印记', 'drop']


def test_run_world_peak_tasks_skips_done():
    """任务页显示已完成的直接跳过，不跑"""
    task = make_task()
    done = {'兽毛之争', '为了荣耀', '世界印记', '世界之心'}
    task.read_task_page_statuses = lambda names: [(n, 'done' if n in done else 'pending') for n in names]
    task.task_completed = lambda name: False
    calls = []
    task.run_world_peak_task = lambda name, kind: calls.append(name) or True
    task.ensure_drop_task = lambda: calls.append('drop') or True

    assert task.run_world_peak_tasks() is True
    assert calls == ['木料之争', '世界之巅', '黄金比例', 'drop']


def test_run_world_peak_tasks_all_done():
    """全做完了：一个都不做"""
    task = make_task()
    task.read_task_page_statuses = lambda names: [(n, 'done') for n in names]
    calls = []
    task.run_world_peak_task = lambda name, kind: calls.append(name) or True
    task.ensure_drop_task = lambda: calls.append('drop') or True

    assert task.run_world_peak_tasks() is True
    assert calls == []


def test_run_world_peak_tasks_stops_on_failure():
    task = make_task()
    task.read_task_page_statuses = lambda names: [(n, 'pending') for n in names]
    calls = []
    task.run_world_peak_task = lambda name, kind: calls.append(name) or (name != '木料之争')
    task.ensure_drop_task = lambda: calls.append('drop') or True

    assert task.run_world_peak_tasks() is False
    assert calls == ['兽毛之争', '木料之争'], calls


def test_run_world_peak_task_dispatch():
    task = make_task()
    task.GATHER_TASKS = (('木料之争', (145, 470)),)
    calls = []
    task.run_gather_task = lambda name, position: calls.append(('gather', name, position)) or True
    task.run_fight_task = lambda name, target, position, corner: \
        calls.append(('fight', name, target, position, corner)) or True
    task.run_glory_task = lambda: calls.append(('glory',)) or True

    assert task.run_world_peak_task('木料之争', 'gather') is True
    assert task.run_world_peak_task('兽毛之争', 'fight') is True
    assert task.run_world_peak_task('为了荣耀', 'glory') is True

    assert calls[0] == ('gather', '木料之争', (145, 470))
    assert calls[1] == ('fight', '兽毛之争', '野兽', (226, 375), 'bottom_right')
    assert calls[2] == ('glory',)

    try:
        task.run_world_peak_task('随便', 'unknown')
    except ValueError:
        pass
    else:
        raise AssertionError('未知玩法类型应该抛 ValueError')


def test_ensure_drop_task_dropped_early():
    """打怪的时候就掉到了：不用重打为了荣耀"""
    task = make_task()
    task.task_completed = lambda name: True
    calls = []
    task.run_glory_task = lambda: calls.append(1) or True

    assert task.ensure_drop_task() is True
    assert calls == []


def test_ensure_drop_task_retries_glory():
    """没掉到：重打为了荣耀，最多 DROP_RETRY 次"""
    task = make_task()
    task.task_completed = lambda name: False
    calls = []
    task.run_glory_task = lambda: calls.append(1) or True

    assert task.ensure_drop_task() is True, '掉落看脸，重试完还不掉也算今天收工'
    assert len(calls) == ScriptTask.DROP_RETRY


def test_ensure_drop_task_stops_when_dropped():
    """重打一轮后掉到了：立刻停，不继续打"""
    task = make_task()
    results = iter([False, False, True])          # 初次 -> 第 1 轮后 -> 第 2 轮后
    task.task_completed = lambda name: next(results, True)
    calls = []
    task.run_glory_task = lambda: calls.append(1) or True

    assert task.ensure_drop_task() is True
    assert len(calls) == 2, calls


def test_ensure_drop_task_gives_up_when_glory_fails():
    task = make_task()
    task.task_completed = lambda name: False
    calls = []
    task.run_glory_task = lambda: calls.append(1) or False

    assert task.ensure_drop_task() is True
    assert len(calls) == 1, '为了荣耀打不动就不要再循环了'


def main():
    cases = [
        test_world_peak_order_table,
        test_run_world_peak_tasks_order,
        test_run_world_peak_tasks_skips_done,
        test_run_world_peak_tasks_all_done,
        test_run_world_peak_tasks_stops_on_failure,
        test_run_world_peak_task_dispatch,
        test_ensure_drop_task_dropped_early,
        test_ensure_drop_task_retries_glory,
        test_ensure_drop_task_stops_when_dropped,
        test_ensure_drop_task_gives_up_when_glory_fails,
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
