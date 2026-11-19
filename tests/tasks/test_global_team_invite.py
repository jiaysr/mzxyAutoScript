# -*- coding: utf-8 -*-
"""全局组队邀请处理测试（不连设备）

覆盖 GlobalGame.handle_team_invite 的分支：
- 策略声明不需要名字时，不调用 OCR，直接接受（WildSnowman 勾选自动接受）
- 策略声明需要名字时，先 OCR，再按名单接受/拒绝
"""
from types import SimpleNamespace

from tasks.GlobalGame.global_game import GlobalGame


class FakeInviteTask:
    I_POPUP_INVITE = object()
    C_POPUP_INVITE_ACCEPT = object()
    C_POPUP_INVITE_CLOSE = object()
    _team_invite_accepted = False

    def __init__(self, accept=True, needs_text=False):
        self.accept = accept
        self.needs_text = needs_text
        self.ocr_calls = 0
        self.clicks = []
        self.device = SimpleNamespace(click_record_clear=lambda: None)

    def appear(self, target):
        return target is self.I_POPUP_INVITE

    def team_invite_needs_text(self):
        return self.needs_text

    def team_invite_text(self):
        self.ocr_calls += 1
        return '月落云渊邀请你加入队伍'

    def team_invite_should_accept(self, invite_text):
        return self.accept

    def click(self, target, interval=None):
        self.clicks.append(target)
        return True


def test_auto_accept_skips_ocr_and_clicks_accept():
    task = FakeInviteTask(accept=True, needs_text=False)
    assert GlobalGame.handle_team_invite(task) is True
    assert task.ocr_calls == 0
    assert task.clicks == [task.C_POPUP_INVITE_ACCEPT]
    assert task._team_invite_accepted is True


def test_whitelist_mode_reads_name_before_accepting():
    task = FakeInviteTask(accept=True, needs_text=True)
    assert GlobalGame.handle_team_invite(task) is True
    assert task.ocr_calls == 1
    assert task.clicks == [task.C_POPUP_INVITE_ACCEPT]


def test_reject_clicks_close_without_accept_flag():
    task = FakeInviteTask(accept=False, needs_text=False)
    assert GlobalGame.handle_team_invite(task) is True
    assert task.ocr_calls == 0
    assert task.clicks == [task.C_POPUP_INVITE_CLOSE]
    assert task._team_invite_accepted is False


def test_no_invite_popup_returns_false():
    task = FakeInviteTask()
    task.appear = lambda target: False
    assert GlobalGame.handle_team_invite(task) is False
    assert task.clicks == []
