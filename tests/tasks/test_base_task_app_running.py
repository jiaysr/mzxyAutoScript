# -*- coding: utf-8 -*-
"""BaseTask 全局游戏进程退出检测回归测试（不连设备）"""
import pytest

from module.exception import GameNotRunningError
from tasks.base_task import BaseTask


class FakeDevice:
    def __init__(self, running=True):
        self.running = running
        self.image = None
        self.checks = 0

    def screenshot(self):
        self.image = object()

    def app_is_running(self, log=True):
        self.checks += 1
        return self.running


class FakeTask(BaseTask):
    # 黑屏检测在别的测试里覆盖，这里只测进程检测
    CHECK_SCREEN_ALIVE = False

    def __init__(self, running=True):
        self.device = FakeDevice(running)

    # 截图流程里的其它全局处理在单测里不需要
    def handle_disconnect(self):
        pass

    def handle_death(self):
        pass

    def handle_captcha(self):
        pass

    def handle_global_popup(self):
        pass


def test_screenshot_raises_when_game_not_running():
    task = FakeTask(running=False)
    with pytest.raises(GameNotRunningError):
        task.screenshot()
    assert task.device.checks == 1


def test_screenshot_passes_when_game_running():
    task = FakeTask(running=True)
    task.screenshot()
    assert task.device.checks == 1


def test_check_is_throttled():
    task = FakeTask(running=False)
    with pytest.raises(GameNotRunningError):
        task.screenshot()
    # 第一次检查后进入间隔节流，5 秒内不再查包名
    task.screenshot()
    assert task.device.checks == 1


def test_switch_off_skips_check():
    task = FakeTask(running=False)
    task.CHECK_APP_RUNNING = False
    task.screenshot()
    assert task.device.checks == 0


def test_restart_task_disables_app_check():
    # 启动/登录阶段前台是 X7 启动器或登录器，不能按游戏包名判断
    from tasks.Restart.restart_base import RestartBase

    assert RestartBase.CHECK_APP_RUNNING is False
