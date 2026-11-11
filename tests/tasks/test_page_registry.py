# -*- coding: utf-8 -*-
"""页面注册回归测试：动态加载 page.py 不能重复注册同名页面"""
from collections import Counter

from tasks.DailySignIn.page import page_fuhua
from tasks.GameUi.core.engine import NavigationEngine
from tasks.GameUi.pages.page import PageRegistry
from tasks.ZhongZhi.page import page_zhongzhi


def test_import_all_pages_does_not_duplicate_pages():
    NavigationEngine._import_all_pages()
    names = [page.name for page in PageRegistry.all()]
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    assert duplicates == []


def test_single_fuhua_page_has_zhongzhi_link():
    # 二义性的 page_fuhua 会让 ui_goto 找不到「种植」tab
    assert page_fuhua.links.get(page_zhongzhi) is not None
