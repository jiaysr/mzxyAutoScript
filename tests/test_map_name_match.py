# -*- coding: utf-8 -*-
"""地图名匹配单测（不连设备）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.GameUi.world.map import MapNavigation


def main():
    matcher = MapNavigation.__new__(MapNavigation)
    cases = [
        ('少狱[单人]', '沙狱[单人]', True),
        ('沙狱单人', '沙狱[单人]', True),
        ('沙狱[多人]', '沙狱[单人]', False),
        ('沙狱外', '沙狱[单人]', False),
        ('万剑家', '万剑冢', True),
        ('域外灵島', '域外灵岛', True),
        ('沙狱外', '沙狱外', True),
        ('帝玉领外', '帝玉岭外', True),
    ]
    for ocr_text, target, expected in cases:
        actual = matcher.map_name_match(ocr_text, target)
        assert actual == expected, f'{ocr_text} vs {target}: {actual} != {expected}'
        print(f'PASS {ocr_text} vs {target} -> {actual}')
    print('---- 8/8 passed ----')


if __name__ == '__main__':
    main()
