# -*- coding: utf-8 -*-
"""自动任务失败现场保存测试（不连设备）

2026-11-16 08:25 真机：麒麟任务卡在 3/5，连续 3 轮进度不动 -> TaskFailed
（`log/error/` 里什么都没有，只有日志，复查不了）。
原因：这类失败是任务**主动** raise TaskFailed，框架的 `save_error_log()`
只在未捕获异常时触发。

这里锁住：
1. `BaseTask.save_debug_frames()` 能把最近几帧 + 日志尾部写到 log/error/<ts>-<tag>/；
2. 自动任务在「等自动任务检测不到自动任务中」和「连续 3 轮进度不动」两处都会存现场；
3. 每次运行最多存 DEBUG_FRAME_SAVE_LIMIT 次，不会刷一堆目录。
"""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from module.exception import TaskFailed
from tasks.AutoTask.config import BonusMultiplier
from tasks.AutoTask.script_task import ScriptTask
from tasks.base_task import BaseTask


# ---------------------------------------------------------------- save_debug_frames
class FakeDevice:
    def __init__(self, frames=2):
        self.screenshot_deque = [
            {'time': datetime(2026, 11, 16, 8, 25, 53 + index, 123456),
             'image': np.full((12, 12, 3), index, dtype=np.uint8)}
            for index in range(frames)
        ]


class FakeBase(BaseTask):
    def __init__(self, save_error=True):
        self.config = SimpleNamespace(
            script=SimpleNamespace(error=SimpleNamespace(save_error=save_error)))
        self.device = FakeDevice()


@pytest.fixture()
def in_tmp_log(tmp_path, monkeypatch):
    """把工作目录切到临时目录，并让 logger 认为日志文件在那里"""
    from module.logger import logger

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'log').mkdir()
    log_file = tmp_path / 'log' / 'fake.txt'
    log_file.write_text('line-1\nline-2\n', encoding='utf-8')
    monkeypatch.setattr(logger, 'log_file', str(log_file), raising=False)
    return tmp_path


def test_save_debug_frames_writes_screenshots_and_log_tail(in_tmp_log):
    folder = FakeBase().save_debug_frames('auto_no_running')
    assert folder
    path = in_tmp_log / folder if not folder.startswith('/') else None
    from pathlib import Path
    folder_path = Path(folder)
    files = sorted(p.name for p in folder_path.iterdir())
    assert len([name for name in files if name.endswith('.png')]) == 2
    assert 'log_tail.txt' in files
    assert 'line-2' in (folder_path / 'log_tail.txt').read_text(encoding='utf-8')


def test_save_debug_frames_respects_save_error_flag(in_tmp_log):
    assert FakeBase(save_error=False).save_debug_frames('x') == ''


def test_save_debug_frames_never_raises(in_tmp_log):
    class Broken(FakeBase):
        def __init__(self):
            self.config = SimpleNamespace(script=SimpleNamespace(
                error=SimpleNamespace(save_error=True)))
            self.device = SimpleNamespace()          # 没有 screenshot_deque 也不该炸

    assert Broken().save_debug_frames('x')


# ---------------------------------------------------------------- 自动任务：两处失败都存现场
class FakeAutoTask(ScriptTask):
    """只测 run() 的「进度不动」分支和现场保存，不碰设备"""

    def __init__(self, progress=None, limit=3):
        self.saved = []
        self.reran = []
        self.config = SimpleNamespace(
            config_name='oas1',
            auto_task=SimpleNamespace(
                auto_task_config=SimpleNamespace(running_check_interval=0)),
            notifier=SimpleNamespace(push=lambda **kwargs: None),
        )
        self.progress = progress or {'麒麟任务': (3, 5)}
        self._resume_at = None
        self._debug_saves = 0
        self.DEBUG_FRAME_SAVE_LIMIT = limit
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    # run() 会调到的外部动作全部抹平
    def ensure_no_team(self, timeout=60):
        pass

    def check_privilege(self, timeout=60):
        pass

    def read_privilege_tasks(self):
        return dict(self.progress)

    def pick_next_task(self, tasks):
        for name, (done, total) in tasks.items():
            if done is not None and total is not None and done < total:
                return name, BonusMultiplier.NONE
        return None

    def ensure_bonus_card(self, *args, **kwargs):
        pass

    def execute_all_auto(self, task_count=1):
        pass

    def execute_task(self, task_name):
        pass

    def save_debug_frames(self, tag='', max_frames=8):
        self.saved.append(tag)
        return f'log/error/fake-{tag}'

    def set_next_run(self, *args, **kwargs):
        pass


def test_stalled_progress_saves_frames_before_failing():
    task = FakeAutoTask()
    with pytest.raises(TaskFailed):
        ScriptTask.run(task)
    # 存了一次现场，名字里带任务名
    assert task.saved == ['auto_stall_麒麟任务']


def test_debug_frame_saves_are_limited_per_run():
    task = FakeAutoTask(limit=0)
    with pytest.raises(TaskFailed):
        ScriptTask.run(task)
    assert task.saved == []            # 超过限额就不再存
