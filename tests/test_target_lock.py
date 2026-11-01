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
