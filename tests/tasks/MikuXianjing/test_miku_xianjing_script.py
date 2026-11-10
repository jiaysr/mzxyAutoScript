# -*- coding: utf-8 -*-
"""迷窟仙境 流程辅助测试（不连设备）"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.MikuXianjing.script_task import ScriptTask


class FakeTimer:
    def __init__(self, reached=True):
        self.reached = reached

    def reached_and_reset(self):
        return self.reached


# ---------------------------------------------------------------- 进度 OCR
def _progress_task(text):
    return SimpleNamespace(
        O_PROGRESS=SimpleNamespace(ocr=lambda image: text),
        device=SimpleNamespace(image='img'),
    )


def test_read_progress_parses_percent():
    assert ScriptTask.read_progress(_progress_task('100%'), 'img') == 100
    assert ScriptTask.read_progress(_progress_task('0%'), 'img') == 0
    assert ScriptTask.read_progress(_progress_task('56%'), 'img') == 56


def test_read_progress_none_on_garbage():
    assert ScriptTask.read_progress(_progress_task(''), 'img') is None
    assert ScriptTask.read_progress(_progress_task('abc'), 'img') is None


# ---------------------------------------------------------------- 停止条件
def _stop_task(percent=0, progress_reached=True, yield_=False, disabled=False):
    return SimpleNamespace(
        reset_records=lambda: None,
        _progress_timer=FakeTimer(progress_reached),
        read_progress=lambda image=None: percent,
        PROGRESS_DONE=100,
        stop_for_others=lambda: yield_,
        stop_because_disabled=lambda: disabled,
    )


def test_stop_complete_at_100():
    assert ScriptTask.battle_stop_reason(_stop_task(percent=100), 'img') == 'complete'


def test_stop_yield():
    assert ScriptTask.battle_stop_reason(_stop_task(percent=50, yield_=True), 'img') == 'yield'


def test_stop_disabled():
    assert ScriptTask.battle_stop_reason(_stop_task(percent=50, disabled=True), 'img') == 'disabled'


def test_stop_none_when_not_done():
    assert ScriptTask.battle_stop_reason(_stop_task(percent=50), 'img') is None


def test_stop_skips_progress_when_timer_not_reached():
    assert ScriptTask.battle_stop_reason(
        _stop_task(percent=100, progress_reached=False), 'img') is None


# ---------------------------------------------------------------- 配置 / 重排
def test_battle_config_points_to_own():
    own = SimpleNamespace(target_names='修仙小妖,修仙刀手')
    task = SimpleNamespace(config=SimpleNamespace(
        miku_xianjing=SimpleNamespace(battle_config=own)))
    assert ScriptTask.yuwai_battle_config(task) is own


class FakeRunTask:
    YIELD_CHECK_INTERVAL = 5
    CONFIG_CHECK_INTERVAL = 30
    PROGRESS_CHECK_INTERVAL = 5
    YIELD_RESUME_SECONDS = 5

    def __init__(self, reason='complete'):
        self.reason = reason
        self.calls = []
        self.config = SimpleNamespace(start_watching=lambda: self.calls.append('watch'))

    def current_config(self):
        return SimpleNamespace()

    def prepare_game(self):
        self.calls.append('prepare')

    def enter_mimang(self, cfg):
        self.calls.append('enter')
        return True

    def progress_battle_loop(self, cfg):
        self.calls.append('battle')
        return self.reason

    def leave_mimang(self, cfg):
        self.calls.append('leave')

    def deliver_quest_after_battle(self, cfg):
        self.calls.append('deliver')

    def set_next_run(self, **kwargs):
        self.calls.append(('schedule', kwargs))


def _schedule(task):
    return [item for item in task.calls if isinstance(item, tuple)
            and item[0] == 'schedule'][0][1]


def test_run_complete_schedules_by_success_interval():
    task = FakeRunTask(reason='complete')
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    schedule = _schedule(task)
    assert schedule['task'] == 'MikuXianjing'
    assert schedule['success'] is True
    assert schedule['finish'] is True
    assert 'target' not in schedule
    assert 'leave' in task.calls
    assert 'deliver' in task.calls


def test_run_yield_schedules_immediate_resume():
    task = FakeRunTask(reason='yield')
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    schedule = _schedule(task)
    assert schedule['task'] == 'MikuXianjing'
    assert schedule['success'] is None
    assert schedule['server'] is False
    assert isinstance(schedule['target'], datetime)
    assert 'leave' in task.calls
    assert 'deliver' not in task.calls


# ---------------------------------------------------------------- 目标名校验（继承）
def test_battle_target_ok_rejects_player():
    task = SimpleNamespace(
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '路过玩家',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['修仙小妖', '修仙刀手']) is False


def test_battle_target_ok_accepts_monster():
    task = SimpleNamespace(
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '修仙小妖',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['修仙小妖', '修仙刀手']) is True


# ---------------------------------------------------------------- 仙心一体标记分类
import cv2
import numpy as np

from tasks.MikuXianjing.assets import MikuXianjingAssets

QUEST_BOX = (555, 413, 745, 437)


def _classify_task(glyph):
    # device.image 为 RGB；把标记字形贴到选项文字左侧，模拟真机一帧
    image = np.full((720, 1280, 3), (20, 30, 45), dtype=np.uint8)
    h, w = glyph.shape[:2]
    image[420:420 + h, 525:525 + w] = glyph
    return SimpleNamespace(
        I_QUEST_AVAILABLE=MikuXianjingAssets.I_QUEST_AVAILABLE,
        I_QUEST_PROGRESS=MikuXianjingAssets.I_QUEST_PROGRESS,
        I_QUEST_DONE=MikuXianjingAssets.I_QUEST_DONE,
        device=SimpleNamespace(image=image),
        screenshot=lambda: None,
    )


def test_classify_marker_available():
    task = _classify_task(MikuXianjingAssets.I_QUEST_AVAILABLE.image)
    assert ScriptTask.classify_quest_marker(task, QUEST_BOX) == 'available'


def test_classify_marker_in_progress():
    task = _classify_task(MikuXianjingAssets.I_QUEST_PROGRESS.image)
    assert ScriptTask.classify_quest_marker(task, QUEST_BOX) == 'in_progress'


def test_classify_marker_complete():
    task = _classify_task(MikuXianjingAssets.I_QUEST_DONE.image)
    assert ScriptTask.classify_quest_marker(task, QUEST_BOX) == 'complete'


# ---------------------------------------------------------------- 仙心一体幂等处理
def _quest_task(state, box=QUEST_BOX):
    clicks = []
    task = SimpleNamespace(
        QUEST_NAME='仙心一体',
        locate_quest_option=lambda name: box,
        classify_quest_marker=lambda b: state,
        device=SimpleNamespace(click=lambda **kw: clicks.append(kw),
                               click_record_clear=lambda: None,
                               sleep=lambda seconds: None),
        click_quest_button=lambda rule, expected: clicks.append(('button', expected)) or True,
        back_to_npc_options=lambda: None,
        O_QUEST_ACTION='action',
    )
    return task, clicks


def test_quest_not_found_means_done_skip():
    task, clicks = _quest_task(None, box=None)
    ScriptTask.handle_xianxin_quest(task)
    assert clicks == []


def test_quest_in_progress_no_reaccept():
    task, clicks = _quest_task('in_progress')
    ScriptTask.handle_xianxin_quest(task)
    assert clicks == []


def test_quest_available_accepts():
    task, clicks = _quest_task('available')
    ScriptTask.handle_xianxin_quest(task)
    assert ('button', ['接受']) in clicks


def test_quest_complete_turns_in():
    task, clicks = _quest_task('complete')
    ScriptTask.handle_xianxin_quest(task)
    assert ('button', ['完成', '领取']) in clicks


def test_quest_non_selected_opens_detail_on_second_click():
    # 非选中项第一下只选中（详情没弹，动作按钮读不到），第二下才弹详情
    option_clicks = []
    action_results = iter([False, True])
    task = SimpleNamespace(
        QUEST_NAME='仙心一体',
        locate_quest_option=lambda name: QUEST_BOX,
        classify_quest_marker=lambda b: 'available',
        device=SimpleNamespace(click=lambda **kw: option_clicks.append(kw),
                               click_record_clear=lambda: None,
                               sleep=lambda seconds: None),
        click_quest_button=lambda rule, expected: next(action_results),
        back_to_npc_options=lambda: None,
        O_QUEST_ACTION='action',
    )
    ScriptTask.handle_xianxin_quest(task)
    assert len(option_clicks) == 2