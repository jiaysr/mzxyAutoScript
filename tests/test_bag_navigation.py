# -*- coding: utf-8 -*-
"""
背包寻物公共层单测（不连设备）

覆盖：
- 模板命中框 -> 格子换算（4 列 x 4 行）
- bag_scan_rule：逐屏匹配、on_hit 提前结束、到底（内容不再变化 / 空格子）停止
- bag_find_item：**优先模板**，模板没命中才退回 OCR
- bag_use_item：点物品 -> 等详情面板出现 -> 点「立即使用」（不再固定等 1s）

    python tests\\test_bag_navigation.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.GameUi.navigation.bag import BagNavigation


class FakeRule:
    """假模板：每次 match_all_any 返回同一批命中"""
    name = 'fake_rule'
    threshold = 0.85

    def __init__(self, hits=None):
        self.hits = list(hits or [])
        self.calls = 0

    def match_all_any(self, image, threshold=None):
        self.calls += 1
        return list(self.hits)


def make_bag(hits=None, signatures=None):
    """只造对象、不跑 __init__：背包公共层的方法都是纯逻辑 + 设备调用，全部打桩"""
    bag = BagNavigation.__new__(BagNavigation)
    bag.device = SimpleNamespace(
        sleep=lambda seconds: None,
        click=lambda x, y, control_name=None: None,
        click_record_clear=lambda: None,
        image=None,
    )
    bag.screenshot = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
    bag.bag_open_ready = lambda: None
    bag.bag_scroll = lambda: None
    bag.appear = lambda rule: rule == 'empty'
    sigs = list(signatures or [])
    bag.bag_page_signature = lambda image: sigs.pop(0) if sigs else b'same'
    bag.bag_region_text = lambda region: '趣味答题券'
    bag.rule = FakeRule(hits)
    return bag


# ------------------------------------------------------------------ 格子换算
def test_cell_from_hit():
    bag = make_bag()
    for column, col_x in enumerate(BagNavigation.BAG_COLUMN_CENTERS):
        for row, row_top in enumerate(BagNavigation.BAG_ROW_TOPS):
            hit_x = col_x - BagNavigation.BAG_PICK_ICON_DX
            hit_y = row_top + BagNavigation.BAG_PICK_ICON_HALF - 20  # 模板高 41 -> 中心约 20
            cell = bag.bag_cell_from_hit(hit_x, hit_y, 214, 41)
            assert cell == (col_x, row_top), (column, row, cell)
    assert bag.bag_cell_center(304, 110) == (304, 135)
    print('test_cell_from_hit OK')


# ------------------------------------------------------------------ 逐屏扫描
def test_scan_stops_on_callback_true():
    bag = make_bag(hits=[(0.92, 279, 114, 214, 41)])
    hits = []

    def on_hit(cell, image, score, hit):
        hits.append((cell, round(score, 2)))
        return True

    assert bag.bag_scan_rule(bag.rule, on_hit=on_hit) is True
    assert hits == [((304, 110), 0.92)], hits
    assert bag.rule.calls == 1, '回调要求提前结束后不该继续翻页'
    print('test_scan_stops_on_callback_true OK')


def test_scan_keeps_going_until_signature_stops():
    bag = make_bag(hits=[], signatures=[b'a', b'b', b'b'])
    cells = []
    assert bag.bag_scan_rule(bag.rule, on_hit=lambda *a: cells.append(a[0])) is False
    assert bag.rule.calls == 3, f'内容不再变化就停：{bag.rule.calls}'
    assert cells == []
    print('test_scan_keeps_going_until_signature_stops OK')


def test_scan_stops_on_empty_cell():
    bag = make_bag(hits=[], signatures=[b'a', b'b', b'c'])
    bag.bag_scan_rule(bag.rule, empty_rule='empty')
    assert bag.rule.calls == 1, '出现空格子就停止翻页'
    print('test_scan_stops_on_empty_cell OK')


def test_scan_dedups_same_cell():
    """同一屏里两个重叠命中框只算一格；第二屏没再命中就按指纹停止"""
    class OnceRule(FakeRule):
        def match_all_any(self, image, threshold=None):
            self.calls += 1
            hits, self.hits = self.hits, []
            return hits

    bag = make_bag()
    bag.rule = OnceRule([(0.95, 279, 114, 214, 41), (0.90, 280, 115, 214, 41)])
    cells = []
    bag.bag_scan_rule(bag.rule, on_hit=lambda *a: cells.append(a[0]))
    assert cells == [(304, 110)], cells
    assert bag.rule.calls == 2, bag.rule.calls
    print('test_scan_dedups_same_cell OK')


# ------------------------------------------------------------------ 找物品
def test_find_item_prefers_template():
    bag = make_bag(hits=[(0.93, 279, 114, 214, 41)])
    ocr_called = []
    bag.bag_find_by_ocr = lambda name: ocr_called.append(name) or (1, 2)
    coord = bag.bag_find_item('趣味答题券', rule=bag.rule)
    assert coord == (304, 134), coord
    assert ocr_called == [], '模板命中就不该再走 OCR'
    print('test_find_item_prefers_template OK')


def test_find_item_rejects_wrong_item():
    """模板命中但格子文字对不上（别的物品长得像）：不能点，继续找/退回 OCR"""
    bag = make_bag(hits=[(0.93, 279, 114, 214, 41)])
    bag.bag_region_text = lambda region: '神秘仙府券'
    bag.bag_scroll_to_top = lambda: None
    bag.bag_find_by_ocr = lambda name: None
    assert bag.bag_find_item('趣味答题券', rule=bag.rule) is None
    print('test_find_item_rejects_wrong_item OK')


def test_find_item_falls_back_to_ocr():
    bag = make_bag(hits=[])
    bag.bag_scroll_to_top = lambda: None
    bag.bag_find_by_ocr = lambda name: (500, 300)
    coord = bag.bag_find_item('趣味答题券', rule=bag.rule)
    assert coord == (500, 300), '模板没命中要退回 OCR'
    print('test_find_item_falls_back_to_ocr OK')


def test_find_item_without_rule_uses_ocr_only():
    bag = make_bag()
    bag.bag_find_by_ocr = lambda name: (1, 1)
    assert bag.bag_find_item('某物品') == (1, 1)
    assert bag.rule.calls == 0
    print('test_find_item_without_rule_uses_ocr_only OK')


# ------------------------------------------------------------------ 使用物品
def test_use_item_waits_for_panel():
    bag = make_bag(hits=[(0.93, 279, 114, 214, 41)])
    calls = {'wait': 0, 'use': 0}
    bag.bag_wait_panel = lambda timeout=None: calls.__setitem__('wait', calls['wait'] + 1) or True
    bag.bag_panel_use = lambda timeout=5: calls.__setitem__('use', calls['use'] + 1) or True
    assert bag.bag_use_item('趣味答题券', rule=bag.rule) is True
    assert calls == {'wait': 1, 'use': 1}, calls
    print('test_use_item_waits_for_panel OK')


def test_use_item_fails_when_panel_missing():
    bag = make_bag(hits=[(0.93, 279, 114, 214, 41)])
    bag.bag_wait_panel = lambda timeout=None: False
    used = []
    bag.bag_panel_use = lambda timeout=5: used.append(1) or True
    assert bag.bag_use_item('趣味答题券', rule=bag.rule) is False
    assert used == [], '面板没出来就别瞎点'
    print('test_use_item_fails_when_panel_missing OK')


def main() -> int:
    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith('test_') or not callable(func):
            continue
        try:
            func()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {name}: {e}')
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f'ERROR {name}: {type(e).__name__}: {e}')
    print('all passed' if not failed else f'{failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
