# -*- coding: utf-8 -*-
"""
任务完成提示监测单测（不连设备）

游戏在左下角聊天栏打 `[系]任务：【跨服】世界之巅已完成`。
监测分两级：先「[系]任务」前缀模板匹配（便宜），命中后再 OCR 抽任务名。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_monitor.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_monitor.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.WorldTask.monitor import (TaskCompleteMonitor, extract_task_name,
                                     normalize_text)


class FakeRule:
    """替身：模板匹配只关心命中与否，OCR 规则只吐预设文本"""

    def __init__(self, hit: bool = True, text: str = ''):
        self.hit = hit
        self.text = text
        self.match_calls = 0
        self.ocr_calls = 0

    def match(self, image, threshold=None):
        self.match_calls += 1
        return self.hit

    def ocr_single_line(self, image):
        self.ocr_calls += 1
        return self.text


def make_monitor(hit=True, text='', dedup=5.0):
    trigger = FakeRule(hit=hit)
    ocr = FakeRule(text=text)
    return TaskCompleteMonitor(trigger, ocr, dedup=dedup), trigger, ocr


# ---------------------------------------------------------------- 文本抽取
def test_extract_task_name():
    cases = [
        ('[系]任务：【跨服】世界之巅已完成', '【跨服】世界之巅'),
        ('[系]任务: 木料之争 已完成', '木料之争'),
        ('［系］任务：【跨服】黄金比例已完成', '【跨服】黄金比例'),
        ('[系]任务：为了荣耀已完成！', '为了荣耀'),
        ('普通聊天：你好啊', None),
        ('[系]任务：', None),
    ]
    for text, expect in cases:
        actual = extract_task_name(text)
        assert actual == expect, f'{text!r} -> {actual!r}, expect {expect!r}'


def test_normalize_text():
    assert normalize_text('任务 ：　世界 之巅') == '任务:世界之巅'


# ---------------------------------------------------------------- 监测器
def test_detect_requires_trigger():
    """一级模板没命中就不做 OCR（省时间）"""
    monitor, trigger, ocr = make_monitor(hit=False, text='任务：世界之巅已完成')
    assert monitor.detect(object()) == []
    assert ocr.ocr_calls == 0, '模板没命中时不该跑 OCR'


def test_detect_reports_once():
    """命中一次报一次，同一任务在去重窗口内不重复报"""
    monitor, trigger, ocr = make_monitor(text='[系]任务：【跨服】世界之巅已完成')
    assert monitor.detect(object()) == ['【跨服】世界之巅']
    assert monitor.detect(object()) == [], '去重窗口内不该重复报'
    assert monitor.names == ['【跨服】世界之巅']


def test_detect_ignores_normal_chat():
    """模板命中但只是普通聊天：不报"""
    monitor, trigger, ocr = make_monitor(text='[系]任务：今天的天气不错')
    assert monitor.detect(object()) == []
    assert monitor.names == []


def test_detect_different_task_after_dedup():
    monitor, trigger, ocr = make_monitor(text='[系]任务：【跨服】木料之争已完成', dedup=0)
    assert monitor.detect(object()) == ['【跨服】木料之争']
    assert monitor.detect(object()) == ['【跨服】木料之争'], 'dedup=0 时每次都报'

    monitor, trigger, ocr = make_monitor(text='[系]任务：【跨服】兽毛之争已完成')
    assert monitor.detect(object()) == ['【跨服】兽毛之争']


def test_detect_handles_none_image():
    monitor, trigger, ocr = make_monitor(text='[系]任务：世界印记已完成')
    assert monitor.detect(None) == []
    assert trigger.match_calls == 0


def main():
    cases = [
        test_extract_task_name,
        test_normalize_text,
        test_detect_requires_trigger,
        test_detect_reports_once,
        test_detect_ignores_normal_chat,
        test_detect_different_task_after_dedup,
        test_detect_handles_none_image,
    ]
    failed = 0
    for case in cases:
        try:
            case()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {case.__name__}: {e}')
        else:
            print(f'PASS {case.__name__}')
    if failed:
        print(f'\n{failed} / {len(cases)} failed')
        sys.exit(1)
    print(f'\nall {len(cases)} passed')


if __name__ == '__main__':
    main()
