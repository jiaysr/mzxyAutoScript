# -*- coding: utf-8 -*-
"""
目标锁定通用能力单测（不连设备）

覆盖：
- 右下角动作按钮分类：红盘攻击 / 绿盘采集 / 蓝盘交互（样本取自 2026-10 真机截图，
  1280x720 下按钮圆盘区域 1147,586,74,73）
- 按钮不可见时返回 None
- 锁定目标雕花模板 I_TARGET_LOCKED 能命中（素材/规则管线没走样）

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_target_lock.py
装了 pytest 时也可以：python -m pytest tests/test_target_lock.py
"""
import os
import sys
import traceback
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.target_action import (ACTION_ATTACK, ACTION_GATHER, ACTION_INTERACT,
                                        classify_action_button, is_attackable)
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.navigation.battle import SimpleBattle
from tasks.GameUi.navigation.top_menu import TopMenuNavigation

ASSET_DIR = PROJECT_ROOT / 'tests' / 'assets' / 'target_lock'
LOCKED_TEMPLATE = PROJECT_ROOT / 'tests' / 'assets' / 'target_locked.png'
# 按钮圆盘在 1280x720 截图里的位置（与 tasks/Battle/slots.py 保持一致）
BUTTON_REGION = (1147, 586, 74, 73)
LOCKED_REGION = (601, 46, 13, 52)


def load_rgb(path: Path) -> np.ndarray:
    """读成 RGB（device.image / RuleImage.image 都是 RGB；cv2.imread 是 BGR）"""
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, f'图片读取失败: {path}'
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def scene_with(patch: np.ndarray, region: tuple) -> np.ndarray:
    """把区域样本贴回 1280x720 黑底截图（验证的是运行时真实坐标）"""
    scene = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = region
    scene[y:y + h, x:x + w] = patch
    return scene


def test_action_button_classification():
    samples = {
        'attack_1.png': ACTION_ATTACK,
        'attack_2.png': ACTION_ATTACK,
        'attack_3.png': ACTION_ATTACK,
        'gather_1.png': ACTION_GATHER,
        'interact_1.png': ACTION_INTERACT,
        'interact_2.png': ACTION_INTERACT,
    }
    for file_name, expected in samples.items():
        patch = load_rgb(ASSET_DIR / file_name)
        action = classify_action_button(scene_with(patch, BUTTON_REGION))
        assert action == expected, f'{file_name}: 期望 {expected}，实际 {action}'


def test_action_button_invisible():
    assert classify_action_button(np.zeros((720, 1280, 3), dtype=np.uint8)) is None


def test_action_attackable():
    assert is_attackable(ACTION_ATTACK)
    assert not is_attackable(ACTION_GATHER)
    assert not is_attackable(ACTION_INTERACT)
    assert not is_attackable(None)


def test_target_locked_template_matches():
    patch = load_rgb(LOCKED_TEMPLATE)
    assert patch.shape[:2][::-1] == LOCKED_REGION[2:], '锁定雕花素材尺寸与规则不一致'

    cwd = os.getcwd()
    os.chdir(PROJECT_ROOT)  # RuleImage.file 是相对项目根目录的路径
    try:
        rule = GameUiAssets.I_TARGET_LOCKED
        assert rule.match(scene_with(patch, LOCKED_REGION)), '锁定雕花应命中'
        assert not rule.match(np.zeros((720, 1280, 3), dtype=np.uint8)), '空白图不应命中'
    finally:
        os.chdir(cwd)


def test_target_name_close_menu():
    task = SimpleBattle.__new__(SimpleBattle)
    calls = []
    task.ui_close_menu = lambda: calls.append('close')
    task.ocr_color_name = (lambda rule, scales=None, min_score=None:
                           calls.append('ocr') or '梦魔兽。')
    assert task.ui_target_name(close_menu=True) == '梦魔兽。'
    assert calls == ['close', 'ocr']
    calls.clear()
    assert task.ui_target_name() == '梦魔兽。'
    assert calls == ['ocr']


class FakeDevice:
    """只实现 ui_ensure_target_locked / battle_relock_target 用到的那几个入口"""

    def __init__(self):
        self.clicks = []

    def stuck_record_clear(self):
        pass

    def click_record_clear(self):
        pass

    def sleep(self, seconds):
        pass

    def click(self, x, y, control_name=None, log=True):
        self.clicks.append((x, y, control_name))


def test_abort_check_stops_close_menu():
    """弹窗压暗画面时，菜单特征认不出来 —— 有 abort_check 就必须立刻放弃，不能再截图空转"""
    task = TopMenuNavigation.__new__(TopMenuNavigation)
    shots = []
    task.screenshot = lambda: shots.append(1)
    task.appear = lambda *args, **kwargs: False
    task.appear_then_click = lambda *args, **kwargs: None

    assert task.ui_close_menu(timeout=6, abort_check=lambda: True) is False
    assert shots == [], f'已中断还在截图空转: {len(shots)} 次'


def test_abort_check_stops_target_lock():
    """结算弹窗弹出来时，锁目标要立刻放弃并返回 False（不能一路空转到超时）"""
    task = SimpleBattle.__new__(SimpleBattle)
    task.device = FakeDevice()
    closed = []
    task.ui_close_menu = lambda **kwargs: closed.append(kwargs.get('abort_check'))
    task.screenshot = lambda: None

    abort = lambda: True
    assert task.ui_ensure_target_locked(max_switch=2, timeout=2,
                                        abort_check=abort) is False
    assert closed == [abort], f'abort_check 没有透传给 ui_close_menu: {closed}'
    assert task.device.clicks == [], f'已中断还去点「目标」按钮: {task.device.clicks}'


def test_relock_max_switch_bounds_attempts():
    """一层只有 4~5 只怪：补锁要能限定只试 1 次，没补到就交回外层（别空切 3 次）"""
    task = SimpleBattle.__new__(SimpleBattle)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.ui_target_attackable = lambda image=None: False

    assert task.battle_relock_target(max_switch=1) is False
    assert len(task.device.clicks) == 1, task.device.clicks
    task.device.clicks.clear()
    assert task.battle_relock_target() is False
    assert len(task.device.clicks) == task.RELOCK_MAX_SWITCH, task.device.clicks


def test_relock_returns_true_when_attackable():
    """补锁到可攻击目标就返回 True，交给战斗循环继续打（不再走完整重锁）"""
    task = SimpleBattle.__new__(SimpleBattle)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.ui_target_attackable = lambda image=None: True

    assert task.battle_relock_target(max_switch=1) is True
    assert len(task.device.clicks) == 1, task.device.clicks


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
