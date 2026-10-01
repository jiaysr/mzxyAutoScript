# -*- coding: utf-8 -*-
"""
仙盟战车参与流程的图片测试：

用 image-test 目录里 2026-09-17 20:00 的真机截图验证：
- 20:00 飞书弹窗截图：能识别到「仙盟战车」文字，能在按钮区域（685,303,958,481）找到「接受」按钮
- 点接受后的截图：地图已经变成仙盟战车，且不再有弹窗文字/接受按钮
- 按图片序列重放 join_chariot 的判断逻辑（点接受 -> 确认地图 -> 重试）

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_war_chariot.py
装了 pytest 时也可以：python -m pytest tests/test_war_chariot.py
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

from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.map import MapNavigation
from tasks.WarChariot.assets import WarChariotAssets
from tasks.WarChariot.script_task import text_match

IMAGE_DIR = r'D:\project\mzxy\image-test'
DIALOG_SHOT = rf'{IMAGE_DIR}\MuMu-20260917-200020-255.png'   # 飞书弹窗（仙盟战车 + 接受按钮）
CHARIOT_SHOT = rf'{IMAGE_DIR}\MuMu-20260917-200028-264.png'  # 点接受后，位于仙盟战车地图
KEYWORD = '仙盟战车'


def read_image(path: str) -> np.ndarray:
    """cv2.imread 不支持中文路径，读进来还要 BGR -> RGB（设备截图是 RGB）"""
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, f'图片读取失败: {path}'
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def load_shots() -> tuple:
    """加载两张测试截图，素材不存在时 pytest 跳过 / 直接运行时报错"""
    if not (os.path.isfile(DIALOG_SHOT) and os.path.isfile(CHARIOT_SHOT)):
        try:
            import pytest
        except ImportError:
            raise SystemExit(f'测试图片不存在: {IMAGE_DIR}')
        pytest.skip(f'测试图片不存在: {IMAGE_DIR}')
    return read_image(DIALOG_SHOT), read_image(CHARIOT_SHOT)


def ocr_texts(rule, image: np.ndarray) -> list:
    return [result.ocr_text for result in rule.detect_and_ocr(image, logDisplay=False)]


def map_location(image: np.ndarray):
    """照抄 map_current_location 的解析逻辑"""
    text = ''.join(ocr_texts(GameUiAssets.O_MAP_LOCATION, image))
    return MapNavigation.map_parse_location(text)


def in_chariot_map(image: np.ndarray) -> bool:
    location = map_location(image)
    if location is None:
        return False
    matcher = MapNavigation.__new__(MapNavigation)
    return matcher.map_name_match(location[0], KEYWORD)


# ---------------------------------------------------------------- 文字识别
def test_text_appears_on_dialog_shot():
    dialog_image, _ = load_shots()
    texts = ocr_texts(WarChariotAssets.O_CHARIOT_TEXT, dialog_image)
    assert any(text_match(text, KEYWORD) for text in texts), f'弹窗截图没识别到{KEYWORD}: {texts}'


def test_text_gone_on_chariot_shot():
    _, chariot_image = load_shots()
    texts = ocr_texts(WarChariotAssets.O_CHARIOT_TEXT, chariot_image)
    assert not any(text_match(text, KEYWORD) for text in texts), f'战车地图不该再有弹窗文字: {texts}'


# ---------------------------------------------------------------- 接受按钮
def test_accept_button_found_on_dialog_shot():
    dialog_image, _ = load_shots()
    rule = WarChariotAssets.I_CHARIOT_ACCEPT
    assert rule.match(dialog_image), '弹窗截图没找到接受按钮'
    x, y, w, h = rule.roi_front
    bx, by, bw, bh = rule.roi_back
    assert bx <= x and by <= y and x + w <= bx + bw and y + h <= by + bh, \
        f'匹配位置 {rule.roi_front} 超出搜索区域 {rule.roi_back}'


def test_accept_button_absent_on_chariot_shot():
    _, chariot_image = load_shots()
    assert not WarChariotAssets.I_CHARIOT_ACCEPT.match(chariot_image), '战车地图不该找到接受按钮'


# ---------------------------------------------------------------- 地图判断
def test_map_is_chariot_on_chariot_shot():
    _, chariot_image = load_shots()
    location = map_location(chariot_image)
    assert location is not None, '战车地图没读到地点文字'
    assert in_chariot_map(chariot_image), f'地点不是{KEYWORD}: {location}'


def test_map_is_not_chariot_on_dialog_shot():
    dialog_image, _ = load_shots()
    location = map_location(dialog_image)
    if location is None:
        return  # 弹窗盖住地点文字时读不到，按未进入战车处理
    assert not in_chariot_map(dialog_image), f'弹窗阶段不该在战车地图: {location}'


# ---------------------------------------------------------------- 流程模拟
class FakeChariotTask:
    """按图片序列重放 join_chariot 的判断逻辑（不连设备）"""

    def __init__(self, images: list):
        self.images = images
        self.index = -1
        self.image = None
        self.clicked = 0

    def screenshot(self):
        self.index = min(self.index + 1, len(self.images) - 1)
        self.image = self.images[self.index]

    def text_appear(self) -> bool:
        texts = ocr_texts(WarChariotAssets.O_CHARIOT_TEXT, self.image)
        return any(text_match(text, KEYWORD) for text in texts)

    def button_appear(self) -> bool:
        return WarChariotAssets.I_CHARIOT_ACCEPT.match(self.image)

    def in_chariot_map(self) -> bool:
        return in_chariot_map(self.image)


def run_join_loop(task: FakeChariotTask, rounds: int = 15) -> bool:
    """
    弹窗阶段：识别到文字 + 找到接受按钮 -> 点击
    点完还没进地图 -> 下一帧重试；地图判断通过 -> 结束
    """
    for _ in range(rounds):
        task.screenshot()
        if task.text_appear() and task.button_appear():
            task.clicked += 1
        if task.in_chariot_map():
            return True
    return False


def test_join_flow_with_images():
    dialog_image, chariot_image = load_shots()
    task = FakeChariotTask([dialog_image, chariot_image])
    entered = run_join_loop(task)
    assert task.clicked == 1, f'接受按钮点击次数不对: {task.clicked}'
    assert entered, '按图片序列没有走到战车地图'


def test_join_flow_times_out_without_chariot_map():
    """点了接受但地图一直没变成战车 -> 应该走超时报错分支"""
    dialog_image, _ = load_shots()
    task = FakeChariotTask([dialog_image] * 3)
    entered = run_join_loop(task)
    assert task.clicked >= 1
    assert not entered


if __name__ == '__main__':
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith('test_') and callable(obj)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f'[FAIL] {name}: {type(exc).__name__}: {exc}')
            traceback.print_exc()
        else:
            print(f'[PASS] {name}')
    print(f'共 {len(tests)} 个测试，失败 {failed} 个')
    raise SystemExit(1 if failed else 0)
