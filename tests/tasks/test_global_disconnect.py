# -*- coding: utf-8 -*-
"""掉线处理回归测试（不连设备）"""
from tasks.GlobalGame.global_game import GlobalGame
from tasks.base_task import BaseTask


class FakeDisconnectTask:
    I_DISCONNECT_DIALOG = 'disconnect_dialog'

    def __init__(self, visible=True):
        self.visible = visible
        self.restarted = 0
        self.resumed = 0

    def appear(self, target, interval=None):
        return self.visible and target == self.I_DISCONNECT_DIALOG

    def restart_game_after_disconnect(self):
        self.restarted += 1

    def handle_death_resume(self):
        self.resumed += 1


def test_disconnect_restarts_then_reuses_death_resume():
    task = FakeDisconnectTask(visible=True)
    GlobalGame.handle_disconnect(task)
    assert task.restarted == 1
    assert task.resumed == 1
    assert GlobalGame._disconnect_restarting is False


def test_disconnect_ignored_when_absent():
    task = FakeDisconnectTask(visible=False)
    GlobalGame.handle_disconnect(task)
    assert task.restarted == 0
    assert task.resumed == 0


def test_disconnect_ignored_while_already_handling():
    task = FakeDisconnectTask(visible=True)
    GlobalGame._disconnect_restarting = True
    try:
        GlobalGame.handle_disconnect(task)
    finally:
        GlobalGame._disconnect_restarting = False
    assert task.restarted == 0
    assert task.resumed == 0


class FakeDeathTask:
    I_DEATH_DIALOG = 'death_dialog'

    def __init__(self):
        self.revived = 0
        self.resumed = 0

    def appear(self, target, interval=None):
        return target == self.I_DEATH_DIALOG

    def revive(self):
        self.revived += 1

    def handle_death_resume(self):
        self.resumed += 1


def test_death_also_goes_through_shared_resume_hook():
    task = FakeDeathTask()
    GlobalGame.handle_death(task)
    assert task.revived == 1
    assert task.resumed == 1


class FakeDevice:
    def __init__(self):
        self.image = None

    def screenshot(self):
        self.image = object()


class FakeOrderTask(BaseTask):
    CHECK_SCREEN_ALIVE = False

    def __init__(self):
        self.device = FakeDevice()
        self.calls = []

    def handle_disconnect(self):
        self.calls.append('disconnect')

    def handle_death(self):
        self.calls.append('death')

    def handle_captcha(self):
        self.calls.append('captcha')

    def handle_global_popup(self):
        self.calls.append('popup')


def test_screenshot_handles_disconnect_before_death():
    task = FakeOrderTask()
    task.screenshot()
    assert task.calls == ['disconnect', 'death', 'captcha', 'popup']
