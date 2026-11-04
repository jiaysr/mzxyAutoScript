# -*- coding: utf-8 -*-
"""
副本页次数识别单测（不连设备）

    python tests\\test_dungeon_counts.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Dungeon.counts import (config_counts, dungeon_name_matches,
                                  normalize_name, parse_dungeon_rows,
                                  parse_privilege_count)


def test_separate_boxes():
    items = [
        ('[2]', 120), ('沙狱[多人]', 121),
        ('[2]', 200), ('沙狱[单人]', 201),
        ('梦', 900),  # 噪声：单独一个字不成行
    ]
    assert parse_dungeon_rows(items) == [('沙狱[多人]', 2), ('沙狱[单人]', 2)]


def test_merged_box_and_fullwidth_brackets():
    items = [
        ('[2] 沙狱[多人]', 120),
        ('【5】', 200), ('白原试炼[双人]', 201),
    ]
    assert parse_dungeon_rows(items) == [('沙狱[多人]', 2), ('白原试炼[双人]', 5)]


def test_name_fix_and_order():
    items = [
        ('[2]', 400), ('帝玉领[多人]', 401),
        ('[2]', 300), ('琉璃幻镜', 301),
    ]
    assert parse_dungeon_rows(items) == [('琉璃幻境', 2), ('帝玉岭[多人]', 2)]
    assert normalize_name('嬴土试炼[双人]') == '赢土试炼[双人]'


def test_rows_with_position():
    items = [('[2]', 120), ('沙狱[多人]', 121), ('[2]', 201), ('沙狱[单人]', 200)]
    assert parse_dungeon_rows(items, with_pos=True) == [
        ('沙狱[多人]', 2, 120),
        ('沙狱[单人]', 2, 200),
    ]


def test_name_match_is_exact_after_fix():
    assert dungeon_name_matches('琉璃幻培', '琉璃幻境')
    assert dungeon_name_matches('帝玉领[多人]', '帝玉岭[多人]')
    assert dungeon_name_matches('帝玉岭[単人]', '帝玉岭[单人]')
    assert not dungeon_name_matches('沙狱[多人]', '沙狱[单人]')


def test_privilege_count_parse():
    assert parse_privilege_count('特权1级额外挑战剩余次数：3') == 3
    assert parse_privilege_count('特权1级额外挑战剩余次数:0') == 0
    assert parse_privilege_count('特权1级额外挑战剩余次数 2') == 2
    assert parse_privilege_count('特权1级额外挑战剩余次数：') is None
    assert parse_privilege_count('') is None


def test_config_mapping_only_known_dungeons():
    counts = {
        '沙狱[多人]': 0,
        '沙狱[单人]': 2,
        '琉璃幻境': 1,
        '仙居福地': 0,
        '白原试炼[双人]': 5,
    }
    assert config_counts(counts) == {
        'shayu_multi': 0,
        'shayu_solo': 2,
        'liuli_huanjing': 1,
    }


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'---- {len(tests)}/{len(tests)} passed ----')
