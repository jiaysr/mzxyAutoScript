# -*- coding: utf-8 -*-
"""
地图名匹配单测（不连设备）

`map_name_match` 容忍 OCR 形近字，但**同一地点的不同变体**必须分开：
沙狱外 / 沙狱[单人] / 沙狱[多人] 前两字完全一样，靠相似度必然互相认错，
所以「单人/多人/双人」和结尾的「外」这两个标记要求两边一致。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_map_name_match.py
装了 pytest 时也可以：python -m pytest tests/test_map_name_match.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.GameUi.world.map import MapNavigation

CASES = [
    # ---- 形近字容错（原有能力，不能坏） ----
    ('少狱[单人]', '沙狱[单人]', True),
    ('沙狱单人', '沙狱[单人]', True),
    ('万剑家', '万剑冢', True),
    ('域外灵島', '域外灵岛', True),
    ('域外灵岛', '域外灵岛', True),      # 「外」不在结尾，不能当成外图标记
    ('帝玉领外', '帝玉岭外', True),
    ('沙狱外', '沙狱外', True),
    # ---- 不同副本/不同变体必须分开 ----
    ('沙狱[多人]', '沙狱[单人]', False),
    ('沙狱外', '沙狱[单人]', False),
    # 2026-11-05 事故：人在沙狱[多人]里，被判成「已经在外图沙狱外」，
    # 于是没走出去 -> 在副本里找万事通找到怪身上 -> 卡死在治疗重试。
    # 根因是 target（沙狱外）本身不带任何变体标记，落到了相似度规则：
    # 「沙狱多」vs「沙狱外」= 0.667 >= 0.6 且前两字相同。
    ('沙狱[多人]', '沙狱外', False),
    ('沙狱[单人]', '沙狱外', False),
    ('沙狱多人', '沙狱外', False),
    ('帝玉岭[多人]', '帝玉岭外', False),
    ('帝玉岭[单人]', '帝玉岭外', False),
    # 反方向也要拦住：在外图时不能判成「已经在副本里」
    ('沙狱外', '沙狱[多人]', False),
    ('帝玉岭外', '帝玉岭[多人]', False),
]


def test_map_name_match():
    matcher = MapNavigation.__new__(MapNavigation)
    failed = []
    for ocr_text, target, expected in CASES:
        actual = matcher.map_name_match(ocr_text, target)
        if actual != expected:
            failed.append(f'{ocr_text} vs {target}: {actual} != {expected}')
    assert not failed, '地图名匹配不符合预期:\n  ' + '\n  '.join(failed)


def main():
    matcher = MapNavigation.__new__(MapNavigation)
    failed = 0
    for ocr_text, target, expected in CASES:
        actual = matcher.map_name_match(ocr_text, target)
        if actual != expected:
            failed += 1
            print(f'FAIL {ocr_text} vs {target}: {actual} != {expected}')
        else:
            print(f'PASS {ocr_text} vs {target} -> {actual}')
    print(f'---- {len(CASES) - failed}/{len(CASES)} passed ----')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
