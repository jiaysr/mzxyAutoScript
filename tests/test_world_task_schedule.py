# -*- coding: utf-8 -*-
"""
世界任务排期单测（不连设备）

开放世界每天 09:00 开门、24:00 关门，任务的下次运行时间必须落在窗口内：
窗口外的排期一律顺延到最近的 09:00，窗口内的排期原样保留。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_schedule.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_schedule.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.WorldTask.schedule import (clamp_to_open_world, in_open_world,
                                      next_run_after)

# (输入时间, 期望的下次运行时间, 说明)
CLAMP_CASES = [
    # ---- 窗口内：原样保留 ----
    ('2026-11-06 09:00:00', '2026-11-06 09:00:00', '开门那一刻'),
    ('2026-11-06 10:30:00', '2026-11-06 10:30:00', '上午'),
    ('2026-11-06 23:59:00', '2026-11-06 23:59:00', '关门前一分钟'),
    # ---- 窗口外：顺延到当天 09:00 ----
    ('2026-11-06 00:00:00', '2026-11-06 09:00:00', '零点'),
    ('2026-11-06 03:20:00', '2026-11-06 09:00:00', '凌晨'),
    ('2026-11-06 08:59:59', '2026-11-06 09:00:00', '开门前一秒'),
    # 跨月也要认日子
    ('2026-11-30 23:59:00', '2026-11-30 23:59:00', '月末窗口内'),
    ('2026-12-01 04:00:00', '2026-12-01 09:00:00', '次月凌晨'),
]

# (现在, 间隔, 期望的下次运行时间, 说明)
NEXT_RUN_CASES = [
    # 每天一次：今天几点做，明天几点（窗口内）
    ('2026-11-06 10:30:00', timedelta(days=1), '2026-11-07 10:30:00', '上午跑完'),
    ('2026-11-06 23:10:00', timedelta(days=1), '2026-11-07 23:10:00', '临近关门'),
    # 加完间隔落在凌晨 -> 顺延到当天开门
    ('2026-11-06 05:00:00', timedelta(days=1), '2026-11-07 09:00:00', '凌晨跑完'),
    ('2026-11-06 08:59:00', timedelta(days=1), '2026-11-07 09:00:00', '开门前跑完'),
    # 失败间隔（小时级）一样受窗口约束
    ('2026-11-06 20:00:00', timedelta(hours=2), '2026-11-06 22:00:00', '失败重试仍在窗口内'),
    ('2026-11-06 22:30:00', timedelta(hours=2), '2026-11-07 09:00:00', '失败重试落到凌晨'),
]

# (时刻, 是否开门)
WINDOW_CASES = [
    ('2026-11-06 00:00:00', False),
    ('2026-11-06 08:59:59', False),
    ('2026-11-06 09:00:00', True),
    ('2026-11-06 12:00:00', True),
    ('2026-11-06 23:59:59', True),
]


def _dt(text: str) -> datetime:
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')


def test_clamp_to_open_world():
    failed = []
    for text, expect, label in CLAMP_CASES:
        actual = clamp_to_open_world(_dt(text))
        if actual != _dt(expect):
            failed.append(f'[{label}] {text} -> {actual} != {expect}')
    assert not failed, '窗口夹取不符合预期:\n  ' + '\n  '.join(failed)


def test_next_run_after():
    failed = []
    for text, interval, expect, label in NEXT_RUN_CASES:
        actual = next_run_after(_dt(text), interval)
        if actual != _dt(expect):
            failed.append(f'[{label}] {text} + {interval} -> {actual} != {expect}')
    assert not failed, '下次运行时间不符合预期:\n  ' + '\n  '.join(failed)


def test_in_open_world():
    failed = []
    for text, expect in WINDOW_CASES:
        actual = in_open_world(_dt(text))
        if actual != expect:
            failed.append(f'{text}: {actual} != {expect}')
    assert not failed, '开门判断不符合预期:\n  ' + '\n  '.join(failed)


def test_float_time_stays_in_window():
    """排期浮动：只在夹到 09:00 整时才浮动，且不能把时间甩出窗口"""
    target = _dt('2026-11-06 03:00:00')
    for _ in range(50):
        clamped = clamp_to_open_world(target, float_seconds=600)
        assert _dt('2026-11-06 09:00:00') <= clamped <= _dt('2026-11-06 09:10:00'), clamped
        assert in_open_world(clamped), clamped
    # 窗口内的排期不浮动
    inside = _dt('2026-11-06 10:30:00')
    assert clamp_to_open_world(inside, float_seconds=600) == inside
    # 浮动时间给得离谱也要留在窗口内
    clamped = clamp_to_open_world(target, float_seconds=100000)
    assert in_open_world(clamped), clamped


def test_task_registered():
    """框架接线：配置模型 / 调度优先级 / 菜单，少一处任务就会静默不跑"""
    from module.config.config_manual import ConfigManual
    from module.config.config_menu import ConfigMenu
    from module.config.config_model import ConfigModel
    from tasks.WorldTask.config import WorldTask

    assert 'world_task' in ConfigModel.model_fields, 'ConfigModel 里没有 world_task'
    assert ConfigModel.model_fields['world_task'].annotation is WorldTask

    # Filter 调度只保留 SCHEDULER_PRIORITY 里列出的任务，漏写 = 永远不跑
    priority = [name.strip() for name in ConfigManual.SCHEDULER_PRIORITY.split('>') if name.strip()]
    assert 'WorldTask' in priority, 'SCHEDULER_PRIORITY 里没有 WorldTask'

    assert 'WorldTask' in ConfigMenu().menu['Daily Task'], 'Daily Task 菜单里没有 WorldTask'

    # 每天一次
    scheduler = WorldTask().scheduler
    assert scheduler.success_interval == timedelta(days=1)
    assert scheduler.failure_interval == timedelta(days=1)


def test_script_task_scheduling_api():
    """ScriptTask 必须走自己的排期（默认 set_next_run 不认开放世界窗口）"""
    from tasks.WorldTask.script_task import ScriptTask

    assert ScriptTask.SCHEDULER_NAME == 'WorldTask'
    for name in ('run', 'schedule_next_run', 'schedule_wait_open_world', 'apply_next_run'):
        assert callable(getattr(ScriptTask, name, None)), f'ScriptTask 缺少 {name}'


def main():
    cases = [
        test_clamp_to_open_world,
        test_next_run_after,
        test_in_open_world,
        test_float_time_stays_in_window,
        test_task_registered,
        test_script_task_scheduling_api,
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
