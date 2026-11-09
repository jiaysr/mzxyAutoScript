# -*- coding: utf-8 -*-
"""每日签到-节点顺序与死亡续跑测试（不连设备）"""
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.DailySignIn.script_task import DailySignInResume, ScriptTask


class FakeRunTask:
    def __init__(self, **flags):
        cfg = SimpleNamespace(
            xiuxin=flags.get('xiuxin', False),
            claim_vip_gift=flags.get('claim_vip_gift', False),
            claim_salary=flags.get('claim_salary', False),
            claim_zodiac_gift=flags.get('claim_zodiac_gift', False),
        )
        self.config = SimpleNamespace(
            daily_sign_in=SimpleNamespace(daily_sign_in_config=cfg))
        self.calls = []
        self.scheduled = []

    def run_step(self, name, func):
        self.calls.append(name)
        return func()

    def do_daily_sign_in(self):
        pass

    def do_claim_vip_gift(self):
        pass

    def do_xiuxin(self):
        pass

    def do_claim_zodiac_gift(self):
        pass

    def do_claim_salary(self):
        pass

    def ensure_back_to_main(self):
        pass

    def set_next_run(self, **kwargs):
        self.scheduled.append(kwargs)


def test_run_order_groups_main_then_panel_steps():
    task = FakeRunTask(xiuxin=True, claim_vip_gift=True,
                       claim_salary=True, claim_zodiac_gift=True)
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    assert task.calls == [
        '领取VIP每日礼包',
        '每日签到',
        '修心',
        '领取生肖福利礼包',
        '领取俸禄',
        '回主页面',
    ]


def test_run_skips_unselected_steps():
    task = FakeRunTask()
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    assert task.calls == ['每日签到', '回主页面']


def test_run_only_salary_still_enters_panel_once():
    task = FakeRunTask(claim_salary=True)
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    assert task.calls == ['每日签到', '领取俸禄', '回主页面']


class FakeStepTask:
    def __init__(self):
        self.calls = 0
        self.resets = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def ui_reset_current_page(self):
        self.resets += 1


def test_run_step_retries_current_node_after_death():
    task = FakeStepTask()

    def step():
        task.calls += 1
        if task.calls == 1:
            raise DailySignInResume
        return 'ok'

    assert ScriptTask.run_step(task, '修心', step) == 'ok'
    assert task.calls == 2
    assert task.resets == 1
    assert task._current_step == '修心'


def test_handle_death_resume_raises_resume():
    task = FakeStepTask()
    with pytest.raises(DailySignInResume):
        ScriptTask.handle_death_resume(task)
    assert task.resets == 1
