# This Python file uses the following encoding: utf-8
"""页面注册表与页面定义：Page / PageRegistry / page_* / 页面连线目标"""
from tasks.GameUi.pages.page import Page, PageRegistry
from tasks.GameUi.pages.targets import (SidebarTarget, TabTarget, MenuTarget,
                                        ActivityTabTarget, ActivitySubTabTarget)

__all__ = ['Page', 'PageRegistry', 'SidebarTarget', 'TabTarget', 'MenuTarget',
           'ActivityTabTarget', 'ActivitySubTabTarget']
