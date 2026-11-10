# -*- coding: utf-8 -*-
"""自动任务-新增每日任务回归测试（不连设备）"""
from types import SimpleNamespace

from tasks.AutoTask.config import BonusMultiplier
from tasks.AutoTask.script_task import ScriptTask


class FakeParseTask:
    TASK_TOTALS = ScriptTask.TASK_TOTALS

    def __init__(self, rows):
        self.rows = rows

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def group_privilege_rows(self):
        return self.rows


def test_new_daily_tasks_totals():
    assert ScriptTask.TASK_TOTALS['忘川每日'] == 12
    assert ScriptTask.TASK_TOTALS['灵岛每日'] == 5
    # 新任务不可使用加量卡
    assert '忘川每日' not in ScriptTask.BONUS_FIELD
    assert '灵岛每日' not in ScriptTask.BONUS_FIELD


def test_parse_unlocked_new_tasks():
    task = FakeParseTask([
        (300, [(100, '修真任务'), (300, '进度: 4/4')]),
        (400, [(100, '忘川每日'), (300, '进度: 0/12')]),
        (500, [(100, '灵岛每日'), (300, '进度: 0/5')]),
    ])
    result = ScriptTask.parse_privilege_tasks(task)
    assert result['忘川每日'] == (0, 12)
    assert result['灵岛每日'] == (0, 5)


def test_parse_locked_new_tasks_are_skipped():
    task = FakeParseTask([
        (400, [(100, '忘川每日'), (300, '70级开启')]),
        (500, [(100, '灵岛每日'), (300, '70级开启')]),
    ])
    result = ScriptTask.parse_privilege_tasks(task)
    assert '忘川每日' not in result
    assert '灵岛每日' not in result


class FakeConfigTask:
    BONUS_FIELD = ScriptTask.BONUS_FIELD
    SKIP_TASKS = ScriptTask.SKIP_TASKS

    def __init__(self):
        self.config = SimpleNamespace(
            auto_task=SimpleNamespace(auto_task_config=SimpleNamespace(
                xiuzhen_bonus=BonusMultiplier.NONE,
                jinqian_bonus=BonusMultiplier.NONE,
                jineng_bonus=BonusMultiplier.NONE,
                jingyan_bonus=BonusMultiplier.NONE,
                zuoqi_bonus=BonusMultiplier.NONE,
                xianmeng_bonus=BonusMultiplier.NONE,
            )))

    def is_task_pending(self, name, done, total):
        return ScriptTask.is_task_pending(self, name, done, total)


def test_new_tasks_are_picked_as_plain_pending_tasks():
    task = FakeConfigTask()
    tasks = {
        '修真任务': (4, 4),
        '忘川每日': (0, 12),
        '灵岛每日': (0, 5),
        '循环任务': (None, None),
    }
    assert ScriptTask.count_pending_tasks(task, tasks) == 2
    assert ScriptTask.pick_next_task(task, tasks) == ('忘川每日', BonusMultiplier.NONE)


def test_new_tasks_finished_means_done():
    task = FakeConfigTask()
    tasks = {
        '忘川每日': (12, 12),
        '灵岛每日': (5, 5),
    }
    assert ScriptTask.count_pending_tasks(task, tasks) == 0
    assert ScriptTask.pick_next_task(task, tasks) is None
