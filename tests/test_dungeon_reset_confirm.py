# -*- coding: utf-8 -*-
"""
「重置副本」确认框与结果浮层单测（不连设备）

事故背景（2026-11-05 02:59，日志 + 错误截图 log/error/1793818740139）：
点了「重置」之后游戏弹出模态框「真的要重置么？（重置副本后，副本的进入次数将减1…）」，
代码没点「确定」，这个模态框就吃掉了之后所有点击 —— 关活动页、切子 tab、点绿X 全部无效，
引擎只能在 page_activity 上反复点绿X，60s 后 `Cannot goto page[page_activity_dungeon]`
→ GameStuckError → 重启游戏，当天整个人副本轮次报废。

重置还有第三种结果（2026-11-05 11:34 真机截图）：次数不足时点重置不弹框，
只显示浮层「无法重置」——不能当成功继续跑。

覆盖：
1. 素材/ROI 回归（真 OCR，样本取自真机截图）：
   - 「真的要重置么？」文案能被识别；
   - 「重置成功」/「无法重置」两种浮层各自命中、且**互不误判**；
   - 普通副本页上那两条判定都不能命中（页面上本来就有「重置」按钮文本）；
   - 「确定」按钮点击点落在真实按钮 727,395-925,462 内。
2. 控制流：确认框要点确定、浮层要关掉、无法重置必须返回 False、没提示不空点。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_dungeon_reset_confirm.py
装了 pytest 时也可以：python -m pytest tests/test_dungeon_reset_confirm.py
"""
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Dungeon.common import DungeonBase

ASSET_DIR = PROJECT_ROOT / 'tests' / 'assets' / 'dungeon_reset'
# 弹窗样本在 1280x720 里的真实位置（真机截图量的）
DIALOG_REGION = (330, 250, 700, 225)
# 结果浮层 ROI（与 O_DUNGEON_RESET_RESULT 一致）
RESULT_REGION = (330, 335, 620, 80)
# 真机实测「确定」按钮外框
CONFIRM_BUTTON = (727, 395, 198, 67)   # x, y, w, h


def load_rgb(path: Path) -> np.ndarray:
    """读成 RGB（device.image / RuleImage.image 都是 RGB；cv2.imread 是 BGR）"""
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, f'图片读取失败: {path}'
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def scene_with(path: Path, region: tuple) -> np.ndarray:
    """把样本贴回 1280x720 黑底截图（OCR 规则用的是绝对坐标）"""
    patch = load_rgb(path)
    scene = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = region
    scene[y:y + h, x:x + w] = patch[:h, :w]
    return scene


def ocr_text(rule, image) -> str:
    return ''.join(item.ocr_text for item in rule.detect_and_ocr(image, logDisplay=False))


# --------------------------------------------------------------------- 素材回归
def test_dialog_text_is_detected():
    """弹窗文案区能识别到「真的要重置」：这是判模态框还在不在的判据"""
    scene = scene_with(ASSET_DIR / 'dialog.png', DIALOG_REGION)
    text = ocr_text(DungeonBase.O_DUNGEON_RESET_DIALOG, scene)
    assert DungeonBase.RESET_DIALOG_TEXT in text, f'没识别到重置弹窗文案: {text!r}'


def test_result_success_is_detected():
    scene = scene_with(ASSET_DIR / 'result_success.png', RESULT_REGION)
    text = ocr_text(DungeonBase.O_DUNGEON_RESET_RESULT, scene)
    assert DungeonBase.RESET_SUCCESS_TEXT in text, f'没识别到「重置成功」: {text!r}'
    assert DungeonBase.RESET_FAILED_TEXT not in text, f'成功浮层被认成了失败: {text!r}'


def test_result_failed_is_detected():
    scene = scene_with(ASSET_DIR / 'result_failed.png', RESULT_REGION)
    text = ocr_text(DungeonBase.O_DUNGEON_RESET_RESULT, scene)
    assert DungeonBase.RESET_FAILED_TEXT in text, f'没识别到「无法重置」: {text!r}'
    assert DungeonBase.RESET_SUCCESS_TEXT not in text, f'失败浮层被认成了成功: {text!r}'


def test_plain_page_is_not_mistaken_for_prompt():
    """
    普通副本页上不能误判

    这块区域平时就有「扫荡」「重置」按钮文本，所以判定必须是完整子串比对：
    换成 RuleOcr 的 keyword 过滤就会踩坑 —— base_ocr.filter 在整串匹配不到时
    会退化成「逐字匹配」，实测「无弹窗」页面上 kw=重置成功 也命中（命中「重置」按钮）。
    """
    scene = scene_with(ASSET_DIR / 'result_page.png', RESULT_REGION)
    text = ocr_text(DungeonBase.O_DUNGEON_RESET_RESULT, scene)
    assert DungeonBase.RESET_SUCCESS_TEXT not in text, f'普通页被认成「重置成功」: {text!r}'
    assert DungeonBase.RESET_FAILED_TEXT not in text, f'普通页被认成「无法重置」: {text!r}'


def test_confirm_button_position():
    """「确定」点击点必须落在真实按钮内（通用 C_DIALOG_CONFIRM 的 y=363 差 43px，点空）"""
    x, y = DungeonBase.C_DUNGEON_RESET_CONFIRM.coord()
    bx, by, bw, bh = CONFIRM_BUTTON
    assert bx <= x <= bx + bw, f'点击点 x={x} 不在按钮 {CONFIRM_BUTTON} 内'
    assert by <= y <= by + bh, f'点击点 y={y} 不在按钮 {CONFIRM_BUTTON} 内'


# --------------------------------------------------------------------- 控制流
class FakeHost(DungeonBase):
    """
    只实现 dungeon_confirm_reset / dungeon_click_reset 需要的东西

    :param state: 'dialog' 确认框 / 'success' 重置成功浮层 / 'failed' 无法重置 / 'page' 什么都没有
    :param dismissible: 点「确定」能不能关掉确认框（模拟点不掉）
    """

    DUNGEON_RESET_CONFIRM_TIMEOUT = 0.05

    def __init__(self, state: str, dismissible: bool = True, detail_ready: bool = True):
        self.state = state
        self.dismissible = dismissible
        self.detail_ready = detail_ready
        self.clicks = []
        self.reset_clicks = 0
        self.device = SimpleNamespace(
            image=object(),
            click_record_clear=lambda: None,
            sleep=lambda seconds: None)

    def screenshot(self):
        return self.device.image

    def dungeon_ocr_text(self, rule):
        if rule is DungeonBase.O_DUNGEON_RESET_DIALOG:
            return '真的要重置么？（重置副本后，副本的进入次数将减1）' if self.state == 'dialog' else ''
        if rule is DungeonBase.O_DUNGEON_RESET_RESULT:
            return {'success': '重置成功', 'failed': '无法重置',
                    'dialog': '', 'page': '扫荡重置'}[self.state]
        raise AssertionError(f'没料到的规则: {rule}')

    def dungeon_detail_ready(self):
        return self.detail_ready

    def appear_then_click(self, rule, **kwargs):
        self.reset_clicks += 1
        self.state = 'dialog'      # 点重置 → 弹出确认框
        return True

    def click(self, button, interval=None):
        self.clicks.append(button.name)
        if button.name == 'dungeon_reset_confirm' and self.dismissible:
            self.state = 'success'          # 点确定 → 重置成功浮层
        elif button.name == 'dungeon_reset_prompt_close':
            self.state = 'page'             # 点绿X → 浮层消失
        return True


def test_confirm_dialog_then_success():
    """正常：确认框 -> 点确定 -> 重置成功浮层 -> 关浮层 -> True"""
    host = FakeHost('dialog')
    assert host.dungeon_confirm_reset() is True
    assert host.clicks == ['dungeon_reset_confirm', 'dungeon_reset_prompt_close'], host.clicks


def test_failed_prompt_returns_false():
    """「无法重置」是明确失败：返回 False，也不该去点确定"""
    host = FakeHost('failed')
    assert host.dungeon_confirm_reset() is False
    assert 'dungeon_reset_confirm' not in host.clicks, host.clicks
    assert 'dungeon_reset_prompt_close' in host.clicks, '应关掉浮层'


def test_no_prompt_means_reset_applied():
    """既没确认框也没浮层：按「重置直接生效」处理，不空点"""
    host = FakeHost('page')
    assert host.dungeon_confirm_reset() is True
    assert host.clicks == [], host.clicks


def test_stuck_dialog_returns_false():
    """
    确认框点不掉必须返回 False

    返回 True 会让调用方继续读次数/开页面，而模态框会把之后所有点击吃掉，
    最后只能等 ui_goto 60s 超时 + GameStuck 重启（就是这次事故）。
    """
    host = FakeHost('dialog', dismissible=False)
    assert host.dungeon_confirm_reset() is False
    assert 'dungeon_reset_confirm' in host.clicks, '应该至少尝试点过一次确定'


def test_click_reset_reports_confirm_failure():
    """dungeon_click_reset 要把确认失败上报成 False，不能假装重置成功"""
    assert FakeHost('dialog', dismissible=False).dungeon_click_reset() is False

    ok = FakeHost('dialog', dismissible=True)
    assert ok.dungeon_click_reset() is True
    assert ok.reset_clicks == 1


def test_click_reset_skips_when_detail_not_ready():
    """右侧详情没就绪时压根不该点重置（保持原行为）"""
    host = FakeHost('page', detail_ready=False)
    assert host.dungeon_click_reset() is False
    assert host.reset_clicks == 0, '详情没好就不该点重置'


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
