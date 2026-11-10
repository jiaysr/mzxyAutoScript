# This file uses the following encoding: utf-8
"""
不周山页面注册：挑战模块下的「不周山」顶部 tab（战场右边第一个）。

挑战模块(战场) -> 不周山；不周山 -> 战场（顶部 tab 互切）
页面特征用左下角「今日剩余挑战次数」文案，次数会变，素材只截到「数」字。
"""
from tasks.Buzhoushan.assets import BuzhoushanAssets as B
from tasks.GameUi.pages.page import Page, page_challenge
from tasks.GameUi.pages.targets import TabTarget

page_buzhoushan = Page(B.I_BUZHOUSHAN_PAGE)
# 标记所属模块：引擎据此自动补出「不周山 -> 各模块首 tab」的侧栏直连
page_buzhoushan.module = '挑战'
page_challenge.link(button=TabTarget('不周山'), destination=page_buzhoushan)
page_buzhoushan.link(button=TabTarget('战场'), destination=page_challenge)
