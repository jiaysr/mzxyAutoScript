# -*- coding: utf-8 -*-
"""
域外迷窟「参与场次 + 11 点世界首领让路」单测（不连设备）

覆盖：
- selected_windows：只参加第一场 / 只参加第二场 / 两场都参加 / 都不参加
- world_boss_yield_times：本任务开关 + 世界首领任务启用 + 勾了 11 点，三个条件都满足才让路
- schedule_after_session：让路收工后排到首领时间（之后按正常优先级排队），否则排下一场
- run()：一个场次都没勾时直接跳过并排期

    python tests\\test_yuwai_schedule.py
"""
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.exception import TaskEnd
from tasks.YuwaiMimang.config import YuwaiBattleConfig
from tasks.YuwaiMimang.script_task import ScriptTask

WINDOWS = [(time(10, 30), time(11, 30)), (time(15, 30), time(16, 30))]
DAY = datetime(2026, 11, 2)
MORNING = (DAY.replace(hour=10, minute=30), DAY.replace(hour=11, minute=30))
AFTERNOON = (DAY.replace(hour=15, minute=30), DAY.replace(hour=16, minute=30))


def make_task(world_boss_enable=True, manchui_11=True, wuqing_19=True):
    """造一个只用来测排期逻辑的对象：世界首领那边的配置用 SimpleNamespace 模拟"""
    task = ScriptTask.__new__(ScriptTask)
    task.config = SimpleNamespace(model=make_model(world_boss_enable, manchui_11, wuqing_19))
    task.next_runs = []
    task.set_next_run = lambda **kwargs: task.next_runs.append(kwargs)
    return task


def make_model(world_boss_enable=True, manchui_11=True, wuqing_19=True):
    return SimpleNamespace(
        world_boss=SimpleNamespace(
            scheduler=SimpleNamespace(enable=world_boss_enable),
            manchui=SimpleNamespace(time_09=False, time_11=manchui_11, time_13=False),
            wuqing=SimpleNamespace(time_10=False, time_12=False, time_19=wuqing_19),
            julian=SimpleNamespace(time_15=False, time_17=False, time_19=False),
            fuwang=SimpleNamespace(time_14=False, time_18=False, time_22=False),
            xiewang=SimpleNamespace(time_21=False),
        ),
        yuwai_mimang=SimpleNamespace(battle_config=YuwaiBattleConfig()),
    )


def make_cfg(join_first=True, join_second=True, join_world_boss=True):
    return SimpleNamespace(
        monster_times='10:30-11:30,15:30-16:30',
        advance_time=SimpleNamespace(hour=0, minute=3, second=0),
        join_first_window=join_first,
        join_second_window=join_second,
        join_world_boss=join_world_boss,
    )


# ------------------------------------------------------------------ 参与场次
def test_selected_windows():
    task = make_task()

    both = task.selected_windows(WINDOWS, make_cfg())
    assert both == WINDOWS, both

    first = task.selected_windows(WINDOWS, make_cfg(join_second=False))
    assert first == [WINDOWS[0]], first

    second = task.selected_windows(WINDOWS, make_cfg(join_first=False))
    assert second == [WINDOWS[1]], second

    none = task.selected_windows(WINDOWS, make_cfg(join_first=False, join_second=False))
    assert none == [], none

    # 第三场及之后没有开关，视为参加
    three = WINDOWS + [(time(20, 0), time(20, 30))]
    assert task.selected_windows(three, make_cfg(join_first=False)) == three[1:], '第三场要保留'
    print('test_selected_windows OK')


# ------------------------------------------------------------------ 世界首领让路
def test_world_boss_yield_requires_both_sides():
    slot = MORNING
    cfg = make_cfg()

    boss_at, leave_at, resume_at = make_task().world_boss_yield_times(slot, cfg)
    assert boss_at == DAY.replace(hour=11), boss_at
    assert leave_at == DAY.replace(hour=10, minute=57), '提前 3 分钟收工'
    assert resume_at == DAY.replace(hour=11), '到首领时间重新排队（之后按正常优先级）'

    # 世界首领任务没启用 -> 不让路
    assert make_task(world_boss_enable=False).world_boss_yield_times(slot, cfg) == (None, None, None)

    # 没勾 11 点那场 -> 不让路
    assert make_task(manchui_11=False).world_boss_yield_times(slot, cfg) == (None, None, None)

    # 本任务自己关掉 -> 不让路
    assert make_task().world_boss_yield_times(slot, make_cfg(join_world_boss=False)) == (None, None, None)

    # 11:00 不在本场（下午场）-> 不让路
    assert make_task().world_boss_yield_times(AFTERNOON, cfg) == (None, None, None)
    print('test_world_boss_yield_requires_both_sides OK')


def test_world_boss_yield_time_edges():
    """首领时间不在本场内就不让路；到点后靠正常优先级排队，不额外加延迟"""
    cfg = make_cfg()

    # 上午 9-10 点那场不跨 11:00
    assert make_task().world_boss_yield_times(
        (DAY.replace(hour=9), DAY.replace(hour=10)), cfg) == (None, None, None)

    # 时段正好在 11:00 结束（10:30-11:00）：11:00 不算落在场次内
    assert make_task().world_boss_yield_times(
        (DAY.replace(hour=10, minute=30), DAY.replace(hour=11)), cfg) == (None, None, None)

    # 时段的开始正好是 11:00（11:00-11:30）：首领时间算在场内
    boss_at, leave_at, resume_at = make_task().world_boss_yield_times(
        (DAY.replace(hour=11), DAY.replace(hour=11, minute=30)), cfg)
    assert boss_at == DAY.replace(hour=11)
    assert leave_at == DAY.replace(hour=10, minute=57)
    assert resume_at == DAY.replace(hour=11), '回场时间 = 首领时间'
    print('test_world_boss_yield_time_edges OK')


# ------------------------------------------------------------------ 收尾排期
def test_schedule_after_session_resumes_after_boss():
    task = make_task()
    task.schedule_next_run = lambda windows=None: task.next_runs.append({'next_window': windows})
    resume = datetime.now() + timedelta(minutes=10)
    task._world_boss_resume = resume

    task.schedule_after_session(WINDOWS)
    assert len(task.next_runs) == 1, task.next_runs
    assert task.next_runs[0]['target'] == resume, task.next_runs
    assert task.next_runs[0]['server'] is False
    assert task._world_boss_resume is None, '排完要清掉，别影响下一次'
    print('test_schedule_after_session_resumes_after_boss OK')


def test_schedule_after_session_without_boss():
    task = make_task()
    task.schedule_next_run = lambda windows=None: task.next_runs.append({'next_window': windows})
    task._world_boss_resume = None

    task.schedule_after_session(WINDOWS)
    assert task.next_runs == [{'next_window': WINDOWS}], task.next_runs
    print('test_schedule_after_session_without_boss OK')


# ------------------------------------------------------------------ run() 入口
def test_run_skips_when_no_window_selected():
    task = make_task()
    cfg = make_cfg(join_first=False, join_second=False)
    task.config.yuwai_mimang = SimpleNamespace(yuwai_mimang_config=cfg)
    task.config.model.yuwai_mimang = task.config.yuwai_mimang
    task.schedule_next_run = lambda windows=None: task.next_runs.append({'next_window': windows})

    try:
        task.run()
        assert False, '应该抛 TaskEnd'
    except TaskEnd:
        pass
    assert len(task.next_runs) == 1 and task.next_runs[0]['next_window'] == WINDOWS, task.next_runs
    print('test_run_skips_when_no_window_selected OK')


def main() -> int:
    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith('test_') or not callable(func):
            continue
        try:
            func()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {name}: {e}')
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f'ERROR {name}: {type(e).__name__}: {e}')
    print('all passed' if not failed else f'{failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
