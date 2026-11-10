# -*- coding: utf-8 -*-
"""种植-任务流程测试（不连设备）"""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from module.exception import GameStuckError, TaskEnd, TaskFailed
from tasks.ZhongZhi.assets import ZhongZhiAssets as Z
from tasks.ZhongZhi.page import page_zhongzhi
from tasks.ZhongZhi.script_task import ScriptTask


class FakeRunTask:
    def __init__(self, goto_ok=True, loop_result=None, loop_error=None):
        self.goto_ok = goto_ok
        self.loop_result = loop_result
        self.loop_error = loop_error
        self.calls = []

    def ui_reset_current_page(self):
        self.calls.append('reset')

    def ui_goto(self, page, timeout=60):
        self.calls.append(('goto', page))
        return self.goto_ok

    def do_planting_loop(self):
        self.calls.append('planting_loop')
        if self.loop_error:
            raise self.loop_error
        return self.loop_result

    def back_to_main(self):
        self.calls.append('back')

    def set_next_run(self, **kwargs):
        self.calls.append(('schedule', kwargs))


def test_run_navigates_and_schedules_after_cooldown():
    target = datetime(2026, 11, 10, 23, 0, 0)
    task = FakeRunTask(loop_result=target)
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    assert task.calls == [
        'reset',
        ('goto', page_zhongzhi),
        'planting_loop',
        'back',
        ('schedule', {'task': 'ZhongZhi', 'success': True, 'finish': True,
                      'target': target, 'server': False}),
    ]


def test_run_still_cleans_up_when_page_missing():
    task = FakeRunTask(goto_ok=False)
    with pytest.raises(GameStuckError):
        ScriptTask.run(task)
    assert 'back' in task.calls
    assert ('schedule', {'task': 'ZhongZhi', 'success': False, 'finish': True,
                         'target': None, 'server': False}) in task.calls


def test_run_marks_failure_when_no_seed():
    task = FakeRunTask(loop_error=TaskFailed('种植无种子'))
    with pytest.raises(TaskFailed):
        ScriptTask.run(task)
    assert ('schedule', {'task': 'ZhongZhi', 'success': False, 'finish': True,
                         'target': None, 'server': False}) in task.calls


class FakeLoopTask:
    I_ZHONGZHI_PAGE = Z.I_ZHONGZHI_PAGE
    I_ZHONGZHI_HELP = Z.I_ZHONGZHI_HELP
    C_ONE_KEY_PLANT = Z.C_ONE_KEY_PLANT
    MAX_PLANT_ROUNDS = 5

    def __init__(self, responses, target=datetime(2026, 11, 10, 23, 0, 0)):
        self.responses = list(responses)
        self.target = target
        self.calls = []
        self.device = SimpleNamespace(sleep=lambda seconds: None,
                                      click_record_clear=lambda: None)

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        return target in (self.I_ZHONGZHI_PAGE, self.I_ZHONGZHI_HELP)

    def click(self, target, interval=None):
        self.calls.append(('click', target))
        return True

    def wait_plant_response(self):
        value = self.responses.pop(0)
        self.calls.append(('response', value))
        return value

    def handle_seed_popup(self):
        self.calls.append('seed')

    def close_plant_popup(self):
        self.calls.append('close_unavailable')

    def land_cooldown_target(self):
        self.calls.append('cooldown_target')
        return self.target


def test_planting_loop_handles_seed_then_unavailable():
    task = FakeLoopTask(['seed', 'unavailable'])
    assert ScriptTask.do_planting_loop(task) == task.target
    assert ('response', 'seed') in task.calls
    assert 'seed' in task.calls
    assert ('response', 'unavailable') in task.calls
    assert 'close_unavailable' in task.calls
    assert 'cooldown_target' in task.calls


def test_planting_loop_fails_when_response_missing():
    task = FakeLoopTask([None])
    with pytest.raises(GameStuckError):
        ScriptTask.do_planting_loop(task)


def test_planting_loop_does_not_treat_click_return_as_result():
    # BaseTask.click 不传 interval 时返回 False，不能因此判定一键种植失败
    task = FakeLoopTask(['unavailable'])
    task.click = lambda target, interval=None: False
    assert ScriptTask.do_planting_loop(task) == task.target


class FakeSeedTask:
    I_SEED_POPUP = Z.I_SEED_POPUP
    I_PLANT_SUCCESS = Z.I_PLANT_SUCCESS
    I_PLANT_POPUP_CLOSE = Z.I_PLANT_POPUP_CLOSE
    C_SEED_CANCEL = Z.C_SEED_CANCEL
    C_SEED_CONFIRM = Z.C_SEED_CONFIRM
    SEED_SLOT_ROIS = ScriptTask.SEED_SLOT_ROIS
    SUCCESS_POPUP_TIMEOUT = 1

    def __init__(self, empty_count=3, seed_name='五行符[种子]', first_slot=(452, 297)):
        self.empty_count = empty_count
        self.seed_name = seed_name
        self.first_slot = first_slot
        self.clicks = []
        self.device_clicks = []
        self.notified = 0
        self.closed_success = 0
        self.device = SimpleNamespace(
            image=np.zeros((720, 1280, 3), dtype=np.uint8),
            click=lambda x=None, y=None, control_name=None: self.device_clicks.append((x, y)),
            click_record_clear=lambda: None,
            sleep=lambda seconds: None,
        )

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def count_empty_seed_slots(self):
        return self.empty_count

    def first_seed_slot(self):
        return self.first_slot

    def read_seed_name(self):
        return self.seed_name

    def notify_no_seed(self):
        self.notified += 1

    def click(self, target, interval=None):
        self.clicks.append(target)
        return True

    def close_plant_success_popup(self):
        self.closed_success += 1


def test_seed_popup_no_seed_notifies_cancels_and_fails():
    task = FakeSeedTask(empty_count=8)
    with pytest.raises(TaskFailed):
        ScriptTask.handle_seed_popup(task)
    assert task.notified == 1
    assert task.clicks == [task.C_SEED_CANCEL]
    assert task.device_clicks == []


def test_seed_popup_clicks_first_seed_reads_name_and_confirms():
    task = FakeSeedTask(empty_count=3)
    ScriptTask.handle_seed_popup(task)
    assert task.notified == 0
    assert task.device_clicks == [task.first_slot]
    assert task.clicks == [task.C_SEED_CONFIRM]
    assert task.closed_success == 1


def test_count_empty_slots_all_empty():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    template = Z.I_SEED_EMPTY_SLOT.image
    for x, y, w, h in ScriptTask.SEED_SLOT_ROIS:
        ox = x + (w - template.shape[1]) // 2
        oy = y + (h - template.shape[0]) // 2
        image[oy:oy + template.shape[0], ox:ox + template.shape[1]] = template
    task = SimpleNamespace(device=SimpleNamespace(image=image),
                           I_SEED_EMPTY_SLOT=Z.I_SEED_EMPTY_SLOT,
                           SEED_SLOT_ROIS=ScriptTask.SEED_SLOT_ROIS,
                           match_in_roi=ScriptTask.match_in_roi)
    assert ScriptTask.count_empty_seed_slots(task) == 8
    assert ScriptTask.first_seed_slot(task) is None


def test_count_empty_slots_and_pick_first_non_empty():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    template = Z.I_SEED_EMPTY_SLOT.image
    for idx, (x, y, w, h) in enumerate(ScriptTask.SEED_SLOT_ROIS):
        ox = x + (w - template.shape[1]) // 2
        oy = y + (h - template.shape[0]) // 2
        if idx == 0:
            image[oy:oy + template.shape[0], ox:ox + template.shape[1]] = 200
        else:
            image[oy:oy + template.shape[0], ox:ox + template.shape[1]] = template
    task = SimpleNamespace(device=SimpleNamespace(image=image),
                           I_SEED_EMPTY_SLOT=Z.I_SEED_EMPTY_SLOT,
                           SEED_SLOT_ROIS=ScriptTask.SEED_SLOT_ROIS,
                           match_in_roi=ScriptTask.match_in_roi)
    assert ScriptTask.count_empty_seed_slots(task) == 7
    assert ScriptTask.first_seed_slot(task) == (452, 297)


class FakeCooldownTask:
    DEFAULT_COOLDOWN_SECONDS = ScriptTask.DEFAULT_COOLDOWN_SECONDS
    COOLDOWN_MARGIN_SECONDS = ScriptTask.COOLDOWN_MARGIN_SECONDS

    def __init__(self, seconds):
        self.seconds = seconds

    def screenshot(self):
        pass

    def read_land_cooldown_seconds(self):
        return self.seconds


def test_land_cooldown_target_uses_detected_seconds():
    task = FakeCooldownTask(100)
    before = datetime.now()
    target = ScriptTask.land_cooldown_target(task)
    delta = (target - before).total_seconds()
    assert 100 + task.COOLDOWN_MARGIN_SECONDS - 2 <= delta <= 100 + task.COOLDOWN_MARGIN_SECONDS + 2


def test_land_cooldown_target_falls_back_to_default():
    task = FakeCooldownTask(None)
    before = datetime.now()
    target = ScriptTask.land_cooldown_target(task)
    delta = (target - before).total_seconds()
    assert (task.DEFAULT_COOLDOWN_SECONDS + task.COOLDOWN_MARGIN_SECONDS - 2
            <= delta
            <= task.DEFAULT_COOLDOWN_SECONDS + task.COOLDOWN_MARGIN_SECONDS + 2)


def test_parse_cooldown_text():
    assert ScriptTask.parse_cooldown_text("07'59'33") == [7 * 3600 + 59 * 60 + 33]
    assert ScriptTask.parse_cooldown_text("07' 59' 56") == [7 * 3600 + 59 * 60 + 56]
    assert ScriptTask.parse_cooldown_text('您当前没有可种植新的土地') == []
    assert ScriptTask.parse_cooldown_text("") == []
    assert ScriptTask.parse_cooldown_text("07'59'33 01'02'03") == [
        7 * 3600 + 59 * 60 + 33,
        1 * 3600 + 2 * 60 + 3,
    ]
