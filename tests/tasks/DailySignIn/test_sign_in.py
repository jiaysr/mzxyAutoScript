# -*- coding: utf-8 -*-
"""每日签到-签到逻辑测试（不连设备）"""
from datetime import datetime
from types import SimpleNamespace

import numpy as np

from tasks.DailySignIn.script_task import ScriptTask


class FakeOcr:
    def __init__(self, text):
        self.text = text

    def ocr(self, image):
        return self.text


class FakeCountTask:
    device = SimpleNamespace(image=object(), sleep=lambda seconds: None)

    def __init__(self, text):
        self.O_REWARD_SIGN_IN_COUNT = FakeOcr(text)

    def screenshot(self):
        pass


def test_read_sign_in_count_parses_digits():
    task = FakeCountTask('本月已累计签到8次')
    assert ScriptTask.read_sign_in_count(task) == 8


def test_read_sign_in_count_returns_none_when_unreadable():
    task = FakeCountTask('本月已累计签到')
    assert ScriptTask.read_sign_in_count(task, retry=1) is None


class FakeSignInTask:
    def __init__(self, count):
        self.count = count
        self.signed = 0
        self.closed = 0

    def ui_reset_current_page(self):
        pass

    def ui_goto(self, page, timeout=60):
        return True

    def open_sign_in_page(self):
        pass

    def read_sign_in_count(self, retry=3):
        return self.count

    def sign_in_today(self, before_count=None):
        self.signed += 1
        return True

    def ensure_back_to_main(self):
        self.closed += 1


def test_daily_sign_in_skips_when_count_equals_today():
    today = datetime.now().day
    task = FakeSignInTask(today)
    ScriptTask.do_daily_sign_in(task)
    assert task.signed == 0
    assert task.closed == 1


def test_daily_sign_in_attempts_when_count_less_than_today():
    today = datetime.now().day
    task = FakeSignInTask(max(0, today - 1))
    ScriptTask.do_daily_sign_in(task)
    assert task.signed == 1
    assert task.closed == 1


def test_daily_sign_in_attempts_when_count_unreadable():
    task = FakeSignInTask(None)
    ScriptTask.do_daily_sign_in(task)
    assert task.signed == 1
    assert task.closed == 1


def test_find_sign_in_slot_scans_top_to_bottom_left_to_right():
    rng = np.random.RandomState(0)
    template = rng.randint(0, 255, (10, 10, 3), dtype=np.uint8)
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    # 第一行两个：左边 (10,10)、右边 (30,10)；第二行 (20,35)
    image[10:20, 10:20] = template
    image[10:20, 30:40] = template
    image[35:45, 20:30] = template

    rule = SimpleNamespace(roi_back=(0, 0, 100, 60), image=template, threshold=0.99)
    task = SimpleNamespace(device=SimpleNamespace(image=image))
    slot = ScriptTask.find_sign_in_slot(task, rule)
    assert slot is not None
    # 先上后下、同排先左后右
    assert slot[2] == (10, 10, 10, 10)


def test_sign_in_slot_signed_detects_changed_border():
    rng = np.random.RandomState(1)
    unsigned = rng.randint(0, 255, (10, 10, 3), dtype=np.uint8)
    signed = rng.randint(0, 255, (10, 10, 3), dtype=np.uint8)
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    image[10:20, 10:20] = unsigned
    rule = SimpleNamespace(image=signed, threshold=0.99)
    box = (10, 10, 10, 10)
    assert ScriptTask.sign_in_slot_signed(image, box, rule) is False

    image[10:20, 10:20] = signed
    assert ScriptTask.sign_in_slot_signed(image, box, rule) is True


class FakeResultTask:
    I_REWARD_SIGN_IN_SIGNED = object()

    def __init__(self, border_result):
        self.border_result = border_result

    def sign_in_slot_signed(self, image, box, signed_rule, pad=8):
        return self.border_result


def test_sign_in_result_prefers_count_increment():
    # 次数可用时：+1 成功，没 +1 失败（即使边框模板会命中也不用）
    task = FakeResultTask(border_result=True)
    assert ScriptTask.sign_in_result(task, None, (0, 0, 1, 1), 2, 3) is True
    assert ScriptTask.sign_in_result(task, None, (0, 0, 1, 1), 2, 2) is False


def test_sign_in_result_falls_back_to_border_when_count_unavailable():
    assert ScriptTask.sign_in_result(FakeResultTask(True), None,
                                     (0, 0, 1, 1), None, 3) is True
    assert ScriptTask.sign_in_result(FakeResultTask(True), None,
                                     (0, 0, 1, 1), 2, None) is True
    assert ScriptTask.sign_in_result(FakeResultTask(False), None,
                                     (0, 0, 1, 1), None, 3) is False


class FakeScrollTask:
    """模拟签到板一直找不到未签到物品"""

    SIGN_IN_SCROLL_MAX = 2
    SIGN_IN_REOPEN_MAX = 1
    I_REWARD_SIGN_IN_UNSIGNED = object()
    S_REWARD_SIGN_IN_SCROLL_DOWN = object()

    def __init__(self, slot_after_misses=None):
        self.swipes = 0
        self.reopens = 0
        self.closed = 0
        self.misses = 0
        self.slot_after_misses = slot_after_misses
        self.device = SimpleNamespace(sleep=lambda seconds: None,
                                      click_record_clear=lambda: None)

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def find_sign_in_slot(self, rule):
        self.misses += 1
        if self.slot_after_misses is not None and self.misses > self.slot_after_misses:
            return (10, 10, (0, 0, 10, 10))
        return None

    def find_unsigned_with_scroll(self, max_scroll=None):
        return ScriptTask.find_unsigned_with_scroll(self, max_scroll)

    def swipe(self, rule):
        self.swipes += 1

    def ensure_back_to_main(self):
        self.closed += 1

    def open_sign_in_page(self):
        self.reopens += 1

    def click_sign_in_slot(self, slot, before_count=None):
        return True


def test_find_unsigned_with_scroll_stops_after_max_scrolls():
    task = FakeScrollTask()
    assert ScriptTask.find_unsigned_with_scroll(task) is None
    assert task.swipes == task.SIGN_IN_SCROLL_MAX
    assert task.misses == task.SIGN_IN_SCROLL_MAX + 1


def test_sign_in_today_reopens_once_then_gives_up():
    task = FakeScrollTask()
    assert ScriptTask.sign_in_today(task) is False
    # 两轮，每轮 2 次滑动
    assert task.swipes == task.SIGN_IN_SCROLL_MAX * 2
    assert task.reopens == 1
    assert task.closed == 1


def test_find_unsigned_with_scroll_returns_when_found():
    task = FakeScrollTask(slot_after_misses=2)
    slot = ScriptTask.find_unsigned_with_scroll(task)
    assert slot is not None
    assert task.swipes == 2
    assert task.misses == 3
