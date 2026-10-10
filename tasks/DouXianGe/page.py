# This Python file uses the following encoding: utf-8
"""
斗仙阁页面注册：挑战模块下的「斗仙阁」顶部 tab（最后一个）。

挑战模块(战场) -> 斗仙阁；斗仙阁 -> 战场（顶部 tab 互切）
"""
from tasks.DouXianGe.assets import DouXianGeAssets as X
from tasks.GameUi.page import Page, page_challenge
from tasks.GameUi.targets import TabTarget

page_douxian = Page(X.I_DOUXIAN_PAGE)
page_challenge.link(button=TabTarget('斗仙阁'), destination=page_douxian)
page_douxian.link(button=TabTarget('战场'), destination=page_challenge)
