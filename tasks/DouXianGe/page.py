# This Python file uses the following encoding: utf-8
"""
斗仙阁页面注册：挑战模块下的「斗仙阁」顶部 tab（最后一个）。

挑战模块(战场) -> 斗仙阁；斗仙阁 -> 战场（顶部 tab 互切）
"""
from tasks.DouXianGe.assets import DouXianGeAssets as X
from tasks.GameUi.pages.page import Page, page_challenge
from tasks.GameUi.pages.targets import TabTarget

page_douxian = Page(X.I_DOUXIAN_PAGE)
# 标记所属模块：引擎据此自动补出「斗仙阁 -> 各模块首 tab」的侧栏直连
page_douxian.module = '挑战'
page_challenge.link(button=TabTarget('斗仙阁'), destination=page_douxian)
page_douxian.link(button=TabTarget('战场'), destination=page_challenge)

