# -*- coding: utf-8 -*-
"""
人机验证（算术题）弹窗处理的图片测试：

用 image-test 目录里 2026-10-17 的两张真机截图验证（同一弹窗两道题）：
- 60 + 4 = ?  ->  答案 64 -> 选项 D（D:64）
- 74 + 7 = ?  ->  答案 81 -> 选项 A（A:81）

覆盖：弹窗特征匹配（别的截图不误命中）、算式/选项识别、字母定位、
点击坐标落在选项框内、以及 handle_captcha 的完整重放（不连设备）。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_global_captcha.py
装了 pytest 时也可以：python -m pytest tests/test_global_captcha.py
"""
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.base.timer import Timer
from module.exception import GameStuckError
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GlobalGame.captcha import CaptchaHandler

IMAGE_DIR = r'D:\project\mzxy\image-test'
# 60 + 4 = ?（答案 64 -> D）
SHOT_CAPTCHA_1 = rf'{IMAGE_DIR}\MuMu-20261017-211015-378.png'
# 74 + 7 = ?（答案 81 -> A）
SHOT_CAPTCHA_2 = rf'{IMAGE_DIR}\MuMu-20261017-175825-617.png'
# 没有弹窗的普通截图（验证不误命中）
SHOT_OTHER = rf'{IMAGE_DIR}\MuMu-20261015-171911-215.png'

# 截图 -> (算式, 选项, 答案字母)
CASES = {
    SHOT_CAPTCHA_1: (
        (60, '+', 4),
        {'A': 27, 'B': 12, 'C': 17, 'D': 64, 'E': 71,
         'F': 95, 'G': 20, 'H': 47, 'I': 85, 'J': 40},
        'D',
    ),
    SHOT_CAPTCHA_2: (
        (74, '+', 7),
        {'A': 81, 'B': 95, 'C': 80, 'D': 92, 'E': 63,
         'F': 22, 'G': 74, 'H': 93, 'I': 87, 'J': 24},
        'A',
    ),
}


def read_image(path: str) -> np.ndarray:
    """cv2.imread 不支持中文路径，读进来还要 BGR -> RGB（设备截图是 RGB）"""
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, f'图片读取失败: {path}'
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def load_images() -> dict:
    """加载测试截图，素材不存在时 pytest 跳过 / 直接运行时报错"""
    missing = [path for path in (SHOT_CAPTCHA_1, SHOT_CAPTCHA_2, SHOT_OTHER)
               if not os.path.isfile(path)]
    if missing:
        try:
            import pytest
        except ImportError:
            raise SystemExit(f'测试图片不存在: {missing}')
        pytest.skip(f'测试图片不存在: {missing}')
    return {path: read_image(path)
            for path in (SHOT_CAPTCHA_1, SHOT_CAPTCHA_2, SHOT_OTHER)}


class FakeDevice:
    """按图片序列重放的假设备：screenshot 出下一帧，click 只记录"""

    def __init__(self, frames: list):
        self.frames = list(frames)
        self.index = -1
        self.image = None
        self.clicks = []
        self.cleared = 0

    def screenshot(self):
        self.index = min(self.index + 1, len(self.frames) - 1)
        self.image = self.frames[self.index]
        return self.image

    def click(self, x=0, y=0, control_name=None, **kwargs):
        self.clicks.append((x, y, control_name))

    def click_record_clear(self):
        self.cleared += 1

    def sleep(self, seconds):
        return None


class FakeNotifier:
    def __init__(self):
        self.pushed = []

    def push(self, title='', content=''):
        self.pushed.append((title, content))


class FakeCaptchaTask(CaptchaHandler):
    """只用 CaptchaHandler 的答题逻辑重放（不连设备、不走 GlobalGame 其它处理）"""

    def __init__(self, device: FakeDevice, notifier: FakeNotifier = None):
        self.device = device
        self.config = SimpleNamespace(config_name='test', notifier=notifier or FakeNotifier())
        # 把弹窗检查的 1 秒限频计时器置成已到期（Windows 时钟精度只有 ~15ms，
        # Timer(0).start() 之后立刻 reached() 可能是 False，clear() 才是确定到期）
        self._captcha_timer = Timer(0).clear()

    def appear(self, target, interval: float = None, threshold: float = None) -> bool:
        """BaseTask.appear 对图片规则的最小实现"""
        return bool(target.match(self.device.image, threshold=threshold))


def option_boxes(image: np.ndarray) -> dict:
    """选项字母 -> OCR 框 (x0, y0, x1, y1)，字母用与实现相同的位置判定"""
    rule = GlobalGameAssets.O_CAPTCHA_OPTIONS
    handler = CaptchaHandler.__new__(CaptchaHandler)
    boxes = {}
    for item in rule.detect_and_ocr(image, logDisplay=False):
        box = item.box
        x0 = int(box[:, 0].min()) + rule.roi[0]
        y0 = int(box[:, 1].min()) + rule.roi[1]
        x1 = int(box[:, 0].max()) + rule.roi[0]
        y1 = int(box[:, 1].max()) + rule.roi[1]
        letter = handler.captcha_letter_at((x0 + x1) / 2, (y0 + y1) / 2)
        if letter:
            boxes[letter] = (x0, y0, x1, y1)
    return boxes


# ---------------------------------------------------------------- 弹窗特征
def test_dialog_found_on_captcha_shots():
    images = load_images()
    for path in (SHOT_CAPTCHA_1, SHOT_CAPTCHA_2):
        rule = GlobalGameAssets.I_CAPTCHA_DIALOG
        assert rule.match(images[path]), f'没识别到人机验证弹窗: {Path(path).name}'


def test_dialog_absent_on_other_shot():
    images = load_images()
    rule = GlobalGameAssets.I_CAPTCHA_DIALOG
    assert not rule.match(images[SHOT_OTHER]), '普通截图不该命中人机验证弹窗'


# ---------------------------------------------------------------- 识别
def test_expression_parsed():
    images = load_images()
    for path, (expression, _, _) in CASES.items():
        task = FakeCaptchaTask(FakeDevice([images[path]]))
        task.device.image = images[path]
        assert task.captcha_parse_expression() == expression, f'{Path(path).name} 算式识别不对'


def test_options_parsed():
    images = load_images()
    for path, (_, options, _) in CASES.items():
        task = FakeCaptchaTask(FakeDevice([images[path]]))
        task.device.image = images[path]
        assert task.captcha_parse_options() == options, f'{Path(path).name} 选项识别不对'


def test_answer_letter():
    images = load_images()
    for path, (expression, _, letter) in CASES.items():
        task = FakeCaptchaTask(FakeDevice([images[path]]))
        task.device.image = images[path]
        result = task.captcha_answer_letter()
        assert result == letter, f'{Path(path).name} 答案字母 {result} != {letter}'


def test_compute_supported_operators():
    assert CaptchaHandler.captcha_compute((6, '+', 4)) == 10
    assert CaptchaHandler.captcha_compute((6, '-', 4)) == 2
    assert CaptchaHandler.captcha_compute((6, 'x', 4)) == 24
    assert CaptchaHandler.captcha_compute((6, 'X', 4)) == 24
    assert CaptchaHandler.captcha_compute((6, '*', 4)) == 24
    assert CaptchaHandler.captcha_compute((6, '×', 4)) == 24
    assert CaptchaHandler.captcha_compute((8, '/', 4)) == 2
    assert CaptchaHandler.captcha_compute((8, '÷', 4)) == 2
    # 除不尽 / 不认识的运算符按失败处理
    assert CaptchaHandler.captcha_compute((7, '÷', 2)) is None
    assert CaptchaHandler.captcha_compute((7, '/', 0)) is None


# ---------------------------------------------------------------- 点击坐标
def test_option_coord_inside_option_box():
    images = load_images()
    for path, (_, _, letter) in CASES.items():
        task = FakeCaptchaTask(FakeDevice([images[path]]))
        boxes = option_boxes(images[path])
        assert letter in boxes, f'{Path(path).name} 没定位到选项 {letter}'
        x, y = task.captcha_option_coord(letter)
        x0, y0, x1, y1 = boxes[letter]
        assert x0 <= x <= x1 and y0 <= y <= y1, \
            f'{Path(path).name} 点击坐标 ({x},{y}) 不在选项 {letter} 框内 {boxes[letter]}'


# ---------------------------------------------------------------- 完整重放
def test_handle_captcha_clicks_right_option():
    images = load_images()
    for path, (_, _, letter) in CASES.items():
        # 第一帧带弹窗，点完第二帧弹窗已消失
        device = FakeDevice([images[path], images[SHOT_OTHER]])
        task = FakeCaptchaTask(device)
        device.screenshot()
        task.handle_captcha()

        x, y = task.captcha_option_coord(letter)
        assert device.clicks == [(x, y, f'captcha_option_{letter}')], \
            f'{Path(path).name} 点击不对: {device.clicks}'
        assert device.cleared, '点完应清一次连点记录'


def test_handle_captcha_ignores_other_shot():
    images = load_images()
    device = FakeDevice([images[SHOT_OTHER]])
    task = FakeCaptchaTask(device)
    device.screenshot()
    task.handle_captcha()
    assert device.clicks == [], '没有弹窗时不该点击'


def test_solve_captcha_failure_notifies():
    """一直解不掉时（同一帧反复重放，模拟点了没反应）：通知 + 按卡死抛错"""
    images = load_images()
    device = FakeDevice([images[SHOT_CAPTCHA_1]])
    notifier = FakeNotifier()
    task = FakeCaptchaTask(device, notifier=notifier)
    device.screenshot()
    try:
        task.solve_captcha()
    except GameStuckError:
        pass
    else:
        raise AssertionError('解不掉时应该抛 GameStuckError')
    assert notifier.pushed, '解不掉时应该推通知'


def test_solve_captcha_no_answer_notifies():
    """选项里没有答案（识别错误）时同样走失败处理"""
    images = load_images()
    device = FakeDevice([images[SHOT_CAPTCHA_1]])
    notifier = FakeNotifier()
    task = FakeCaptchaTask(device, notifier=notifier)
    device.screenshot()
    # 让算式识别对不上选项：直接把选项识别结果改掉
    task.captcha_parse_options = lambda: {'A': 1, 'B': 2}
    try:
        task.solve_captcha()
    except GameStuckError:
        pass
    else:
        raise AssertionError('选项里没有答案时应该抛 GameStuckError')
    assert notifier.pushed, '解不掉时应该推通知'


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001  # SystemExit(pytest.skip) 也在这里
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
