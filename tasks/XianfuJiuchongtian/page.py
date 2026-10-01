# This Python file uses the following encoding: utf-8
"""
仙府九重天页面注册：挑战模块下的「仙府九重天」顶部 tab。

挑战模块(战场) -> 仙府九重天；仙府九重天 -> 战场（顶部 tab 互切）
"""
from tasks.GameUi.page import Page, page_challenge
from tasks.GameUi.targets import TabTarget
from tasks.XianfuJiuchongtian.assets import XianfuJiuchongtianAssets as X

page_xianfu = Page(X.I_XIANFU_PAGE)
page_challenge.link(button=TabTarget('仙府九重天'), destination=page_xianfu)
page_xianfu.link(button=TabTarget('战场'), destination=page_challenge)
