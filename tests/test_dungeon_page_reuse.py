# -*- coding: utf-8 -*-
"""
副本页复用（快速路径）单测（不连设备）

背景：`dungeon_open_page()` 一整套流程（关活动弹窗 -> 回主页 -> 右上菜单 -> 活动 ->
活动 tab -> 滑到副本子 tab）实测约 8s，而 2026-11-05 一次「重置提交」重开了 3 次：
  1. 读次数          2. 选中副本       3. 重置后校验次数
其中 2、3 都发生在副本页已经打开、目标行就在当前屏幕上的时候 —— 没必要重开。

所以给 field_status / select 加了快速路径：当前就在副本页且行可见 -> 直接用；
否则**必须回退**到原来的「关掉重开 + 逐屏下滑」，不能因为省时间而扫错。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_dungeon_page_reuse.py
装了 pytest 时也可以：python -m pytest tests/test_dungeon_page_reuse.py
"""
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Dungeon.common import DungeonBase

TARGET = '帝玉岭[多人]'
FIELD = 'diyuling_multi'


class FakeHost(DungeonBase):
    """只实现快速路径和回退分支需要的东西"""

    DUNGEON_SCAN_MAX_SCREENS = 1

    def __init__(self, page_opened: bool, rows: list, click_verified: bool = True):
        self.page_opened = page_opened
        self.rows = list(rows)
        self.click_verified = click_verified
        self.clicks = []
        self.open_page_calls = 0
        self.scan_calls = 0
        self.device = SimpleNamespace(
            click=lambda x, y, control_name=None: self.clicks.append((x, y)),
            click_record_clear=lambda: None,
            sleep=lambda seconds: None)

    # ---- 页面 / 列表 ----
    def screenshot(self):
        return object()

    def appear(self, rule, **kwargs):
        return self.page_opened

    def dungeon_read_rows(self):
        return list(self.rows)

    def dungeon_open_page(self, timeout: int = 60) -> bool:
        self.open_page_calls += 1
        self.page_opened = True
        return True

    def dungeon_scan_counts(self, max_screens=None, stop_name=None):
        self.scan_calls += 1
        return {TARGET: 3}

    def dungeon_privilege_count(self):
        return 1

    # ---- 行的点选确认 ----
    def dungeon_row_selected(self, name: str, fallback_y: int) -> bool:
        return self.click_verified

    def ui_swipe_gentle(self, *args, **kwargs):
        pass


def visible_rows():
    return [('沙狱[多人]', 0, 220), ('沙狱[单人]', 0, 301), (TARGET, 2, 395)]


# --------------------------------------------------------------------- 读计数
def test_visible_count_reads_current_screen():
    host = FakeHost(page_opened=True, rows=visible_rows())
    assert host.dungeon_visible_count(TARGET) == 2


def test_visible_count_none_when_page_closed():
    host = FakeHost(page_opened=False, rows=visible_rows())
    assert host.dungeon_visible_count(TARGET) is None, '页面没开就不能读当前屏'


def test_visible_count_none_when_row_missing():
    host = FakeHost(page_opened=True, rows=[('沙狱[多人]', 0, 220)])
    assert host.dungeon_visible_count(TARGET) is None, '行不在屏幕上应返回 None，交给完整扫描'


def test_field_status_uses_fast_path():
    host = FakeHost(page_opened=True, rows=visible_rows())
    count, privilege = host.dungeon_field_status(FIELD)
    assert count == 2
    assert privilege == 1
    assert host.scan_calls == 0, '当前屏能读到就别再整页扫'
    assert host.open_page_calls == 0, '不该重开副本页'


def test_field_status_falls_back_to_full_scan():
    """页面没开（跑完副本刚回外图）：必须走原来的整页扫描，不能读到旧数据"""
    host = FakeHost(page_opened=False, rows=visible_rows())
    count, _ = host.dungeon_field_status(FIELD)
    assert host.scan_calls == 1
    assert count == 3, '应使用完整扫描的结果'


def test_field_status_falls_back_when_row_missing():
    host = FakeHost(page_opened=True, rows=[('沙狱[单人]', 0, 301)])
    count, _ = host.dungeon_field_status(FIELD)
    assert host.scan_calls == 1, '行不在当前屏要回退整页扫描'
    assert count == 3


# --------------------------------------------------------------------- 选中行
def test_select_clicks_visible_row_without_reopen():
    host = FakeHost(page_opened=True, rows=visible_rows())
    assert host.dungeon_select(TARGET) is True
    assert host.open_page_calls == 0, '当前屏就有这一行，不该重开副本页'
    assert len(host.clicks) == 1, host.clicks


def test_select_reopens_when_row_not_visible():
    """行不在当前屏：必须重开副本页从顶部找（列表不会自己滚回去）"""
    host = FakeHost(page_opened=True, rows=[('沙狱[单人]', 0, 301)])
    assert host.dungeon_select(TARGET) is False
    assert host.open_page_calls == 1, '找不到行就得重开副本页'


def test_select_reopens_when_page_closed():
    host = FakeHost(page_opened=False, rows=visible_rows())
    assert host.dungeon_select(TARGET) is True
    assert host.open_page_calls == 1, '页面没开时必须正常打开'


def test_select_returns_false_when_click_not_verified():
    host = FakeHost(page_opened=True, rows=visible_rows(), click_verified=False)
    assert host.dungeon_select(TARGET) is False


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
