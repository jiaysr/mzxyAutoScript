# -*- coding: utf-8 -*-
"""每日签到-修心流程测试（不连设备）"""
from types import SimpleNamespace

from tasks.DailySignIn.script_task import ScriptTask


class FakeXiuxinTask:
    XIUXIN_MAX_CLICKS = 3
    I_XIUXIN_FINISHED = 'finished'
    I_CLOSE_X_GREEN = 'close_x'
    C_XIUXIN_BUTTON = object()

    def __init__(self, finish_after_clicks):
        self.finish_after_clicks = finish_after_clicks
        self.clicks = 0
        self.popup_closed = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None,
                                      click_record_clear=lambda: None)

    def ui_reset_current_page(self):
        pass

    def ui_goto(self, page, timeout=60):
        return True

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        if target is self.I_XIUXIN_FINISHED:
            return self.clicks >= self.finish_after_clicks
        if target is self.I_CLOSE_X_GREEN:
            return True
        return False

    def click(self, target, interval=None):
        self.clicks += 1
        return True

    def appear_then_click(self, target, interval=None):
        self.popup_closed += 1
        return True


def test_xiuxin_skips_clicks_when_already_finished():
    task = FakeXiuxinTask(finish_after_clicks=0)
    ScriptTask.do_xiuxin(task)
    assert task.clicks == 0
    assert task.popup_closed == 0


def test_xiuxin_stops_when_finished_before_max_clicks():
    task = FakeXiuxinTask(finish_after_clicks=2)
    ScriptTask.do_xiuxin(task)
    assert task.clicks == 2
    assert task.popup_closed == 2


def test_xiuxin_clicks_max_times_then_judges_finished():
    task = FakeXiuxinTask(finish_after_clicks=999)
    ScriptTask.do_xiuxin(task)
    assert task.clicks == task.XIUXIN_MAX_CLICKS
    assert task.popup_closed == task.XIUXIN_MAX_CLICKS
