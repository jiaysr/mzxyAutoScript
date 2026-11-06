# -*- coding: utf-8 -*-
"""BaseTask 全局黑屏检测回归测试（不连设备）"""
import numpy as np
import pytest

from module.exception import GameNotRunningError
from tasks.base_task import BaseTask

BLACK = np.zeros((8, 8, 3), dtype=np.uint8)
BRIGHT = np.full((8, 8, 3), 200, dtype=np.uint8)


class FakeDevice:
    def __init__(self, images):
        self.images = list(images)
        self.image = None

    def screenshot(self):
        self.image = self.images.pop(0) if self.images else BRIGHT


class FakeTask(BaseTask):
    def __init__(self, images):
        self.device = FakeDevice(images)
        self._black_strikes = 0

    # 截图流程里的全局处理在单测里不需要
    def handle_death(self):
        pass

    def handle_captcha(self):
        pass

    def handle_global_popup(self):
        pass


def test_black_screen_raises_after_consecutive_strikes():
    task = FakeTask([BLACK] * 5)
    for _ in range(BaseTask.BLACK_STRIKES - 1):
        task.screenshot()          # 还没到阈值，不抛
    with pytest.raises(GameNotRunningError):
        task.screenshot()


def test_bright_frame_resets_strikes():
    task = FakeTask([BLACK, BLACK, BRIGHT, BLACK, BLACK, BLACK, BLACK, BLACK])
    for image in task.device.images[:-1]:
        task.screenshot()
    # 中间有一帧亮屏，计数已清零；后面 5 连黑才触发
    with pytest.raises(GameNotRunningError):
        task.screenshot()


def test_switch_off_skips_check():
    task = FakeTask([BLACK] * 10)
    task.CHECK_SCREEN_ALIVE = False
    for _ in range(10):
        task.screenshot()          # 关掉检测后黑屏也不抛


def test_restart_task_disables_black_check():
    # 登录/启动阶段本来就长时间黑屏，Restart 任务必须关掉
    from tasks.Restart.restart_base import RestartBase

    assert RestartBase.CHECK_SCREEN_ALIVE is False
