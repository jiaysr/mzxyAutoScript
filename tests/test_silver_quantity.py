# -*- coding: utf-8 -*-
"""
银贝礼盒「调数量」逻辑测试（不连设备，纯逻辑）

    toolkit\\python.exe -m pytest tests/test_silver_quantity.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tasks.SilverGiftBox.script_task import ScriptTask  # noqa: E402


class FakeQuantityTask(ScriptTask):
    """用内存变量模拟数量弹窗：点减号 -1，在 1 时回绕到 max"""

    def __init__(self, current: int, max_n: int):
        self.value = current          # 当前数量
        self.max_n = max_n            # 回绕上限
        self.clicks = []              # 点击记录，元素是坐标
        self.unreadable = False       # 模拟读不出来

    # --- 覆盖真实实现 ---
    def quantity_current(self):
        return None if self.unreadable else self.value

    def click_minus(self, coord: tuple, delay: float = 1.0) -> None:
        self.clicks.append(tuple(coord))
        step = 10 if tuple(coord) == tuple(self.MINUS_BIG) else 1
        value = self.value - step
        while value < 1:
            value += self.max_n
        self.value = value


def make_task(current, max_n) -> FakeQuantityTask:
    return FakeQuantityTask(current=current, max_n=max_n)


def test_quantity_already_at_target():
    task = make_task(current=1, max_n=1)

    assert task.quantity_set(1, 1) is True
    assert task.clicks == []


def test_quantity_steps_down_with_small_minus():
    task = make_task(current=4, max_n=4)

    assert task.quantity_set(1, 4) is True
    assert task.value == 1
    assert task.clicks == [ScriptTask.MINUS_SMALL] * 3


def test_quantity_wraps_from_one_to_max():
    task = make_task(current=1, max_n=4)

    assert task.quantity_set(4, 4) is True
    assert task.value == 4
    assert task.clicks == [ScriptTask.MINUS_SMALL]


def test_quantity_uses_big_minus_for_tens():
    task = make_task(current=25, max_n=30)

    assert task.quantity_set(5, 30) is True
    assert task.value == 5
    assert task.clicks == [ScriptTask.MINUS_BIG] * 2


def test_quantity_aborts_when_unreadable():
    task = make_task(current=4, max_n=4)
    task.unreadable = True

    assert task.quantity_set(1, 4) is False
    assert task.clicks == []


def test_quantity_rejects_invalid_max():
    task = make_task(current=1, max_n=0)

    assert task.quantity_set(1, 0) is False
    assert task.clicks == []


def test_quantity_rejects_out_of_range_value():
    task = make_task(current=9, max_n=4)

    assert task.quantity_set(1, 4) is False
    assert task.clicks == []


def test_buy_confirm_checks_count_range(monkeypatch):
    """count 超出 1..max 时直接失败，不点确定"""
    task = make_task(current=1, max_n=1)
    task.quantity_appear = lambda: True
    monkeypatch.setattr(task, 'quantity_max', lambda: 1, raising=False)
    clicked = []
    monkeypatch.setattr(task, 'dialog_confirm_coord', lambda: (clicked.append(1), (100, 100))[1], raising=False)

    assert task.buy_confirm(count=2) is False
    assert clicked == []


if __name__ == '__main__':
    pytest.main([__file__, '-q'])
