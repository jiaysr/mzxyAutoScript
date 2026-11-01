# -*- coding: utf-8 -*-
"""
页面图侧栏规则单测（纯逻辑，不连设备）

覆盖：
- 模块首 tab 页标记（Page.module_home）与子页所属模块（Page.module）
- 「面板页 -> 各模块首 tab」的侧栏直连由规则自动补出，不必逐条写 links
- 子页去其他模块走直连（不再先切回本模块首 tab）
- 子页「返回」直连主页面（不再绕进别的模块）
- 显式 links 优先于规则
- 页面图连通性：所有页面都能回主页面；页面名不重复

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_page_links.py
装了 pytest 时也可以：python -m pytest tests/test_page_links.py
"""
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.GameUi.core.engine import NavigationEngine
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.pages.page import (PageRegistry, page_challenge, page_item_bag,
                                     page_main, page_role_detail, page_task, page_team)
from tasks.GameUi.pages.targets import SidebarTarget, TabTarget
import tasks.AutoTask.page as autotask_page  # noqa: F401  注册任务子页
import tasks.DouXianGe.page as douxian_page  # noqa: F401
import tasks.XianfuJiuchongtian.page as xianfu_page  # noqa: F401
from tasks.AutoTask.page import page_bonus, page_privilege
from tasks.DouXianGe.page import page_douxian
from tasks.XianfuJiuchongtian.page import page_xianfu

MODULE_HOMES = {
    '物品': page_item_bag,
    '挑战': page_challenge,
    '任务': page_task,
    '队伍': page_team,
    '角色': page_role_detail,
}
SUB_PAGES = (page_privilege, page_bonus, page_douxian, page_xianfu)


class _PageGraph:
    """只借用引擎的页面图方法，不初始化 config/device"""

    @property
    def ui_pages(self):
        return PageRegistry.all()

    ui_module_homes = NavigationEngine.ui_module_homes
    ui_page_links = NavigationEngine.ui_page_links
    build_reverse_path_dict = NavigationEngine.build_reverse_path_dict


def graph() -> _PageGraph:
    return _PageGraph()


def test_module_homes():
    assert graph().ui_module_homes() == MODULE_HOMES


def test_sub_pages_declare_module():
    assert page_privilege.module == '任务'
    assert page_bonus.module == '任务'
    assert page_douxian.module == '挑战'
    assert page_xianfu.module == '挑战'


def test_sidebar_rule_adds_direct_links():
    graph_ = graph()
    for page in SUB_PAGES:
        links = graph_.ui_page_links(page)
        for module, home in MODULE_HOMES.items():
            assert home in links, f'{page} 缺少到 {home} 的侧栏直连'
            if not isinstance(links[home], TabTarget):
                assert isinstance(links[home], SidebarTarget)
                assert links[home].name == module
    assert graph_.ui_page_links(page_privilege)[page_item_bag].name == '物品'
    assert graph_.ui_page_links(page_douxian)[page_item_bag].name == '物品'


def test_explicit_links_win():
    """显式写死的按钮优先，规则只补缺失的模块边"""
    links = graph().ui_page_links(page_privilege)
    assert isinstance(links[page_task], TabTarget)
    assert links[page_task].name == '任务'


def test_same_module_tabs_are_direct():
    """任务特权 <-> 任务加量 顶部 tab 直达，不再绕回任务首 tab"""
    graph_ = graph()
    assert graph_.ui_page_links(page_privilege)[page_bonus].name == '任务加量'
    assert graph_.ui_page_links(page_bonus)[page_privilege].name == '任务特权'
    assert graph_.build_reverse_path_dict(page_bonus)[page_privilege] == [page_privilege, page_bonus]
    assert graph_.build_reverse_path_dict(page_privilege)[page_bonus] == [page_bonus, page_privilege]


def test_sub_page_to_other_module_is_direct():
    """子页去别的模块不再绕回本模块首 tab"""
    paths = graph().build_reverse_path_dict(page_item_bag)
    for page in SUB_PAGES:
        assert paths[page] == [page, page_item_bag], f'{page} 应直连到物品页'


def test_sub_page_can_go_main_directly():
    """子页点右上角「返回」直接回主页面，不绕进别的模块"""
    graph_ = graph()
    paths = graph_.build_reverse_path_dict(page_main)
    for page in SUB_PAGES:
        assert paths[page] == [page, page_main]
        assert graph_.ui_page_links(page)[page_main] is GameUiAssets.C_PAGE_PLAYER_BACK


def test_all_pages_can_reach_main():
    paths = graph().build_reverse_path_dict(page_main)
    reachable = {page.name for page in paths}
    missing = {page.name for page in PageRegistry.all()} - reachable
    assert not missing, f'无法回主页面: {sorted(missing)}'


def test_page_names_unique():
    names = [page.name for page in PageRegistry.all()]
    assert len(names) == len(set(names))


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
