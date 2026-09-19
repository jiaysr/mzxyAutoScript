# This Python file uses the following encoding: utf-8
from itertools import compress

import random

import traceback
from module.atom.click import RuleClick
from tasks.GameUi.assets import GameUiAssets as G


class PageRegistry:
    _registry = []

    @classmethod
    def register(cls, page):
        cls._registry.append(page)

    @classmethod
    def all(cls):
        return list(cls._registry)


class Page:
    def __init__(self, check_button, links=None):
        if links is None:
            links = {}
        self.check_button = check_button
        self.links = links
        self.additional: list = None  # 附加按钮或者是ocr检测按钮
        (filename, line_number, function_name, text) = traceback.extract_stack()[-2]
        self.name = text[:text.find('=')].strip()
        PageRegistry.register(self)

    def __eq__(self, other):
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        return self.name

    def link(self, button, destination):
        self.links[destination] = button


class SidebarTarget:
    """
    页面连线用目标：在角色面板左侧可滚动模块栏中 OCR 查找并点击模块
    由 GameUi.appear_then_operate 识别处理
    """

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return f'sidebar[{self.name}]'


class TabTarget:
    """
    页面连线用目标：在当前模块顶部可横向滚动的 tab 栏中 OCR 查找并点击 tab
    由 GameUi.appear_then_operate 识别处理
    """

    def __init__(self, name: str, module: str = None):
        self.name = name
        self.module = module

    def __str__(self):
        return f'tab[{self.module or ""}{self.name}]'


# ************************************* 明珠轩辕页面注册区 *****************************************#
# 物品-背包
page_item_bag = Page(G.I_PAGE_ITEM_BAG)
# 挑战-战场（挑战模块首页）
page_challenge = Page(G.I_PAGE_CHALLENGE)
# 角色-详情（角色模块首页，主页面点头像进入）
page_role_detail = Page(G.I_PAGE_ROLE_DETAIL)
# 主页面（野外/主界面）
page_main = Page(G.I_PAGE_MAIN)

# 页面所属模块（供 tab 导航判断滚动方向）
page_item_bag.module = '物品'
page_challenge.module = '挑战'
page_role_detail.module = '角色'

page_main.link(button=G.C_PAGE_MAIN_GOTO_PLAYER, destination=page_role_detail)
page_role_detail.link(button=G.C_PAGE_PLAYER_BACK, destination=page_main)
page_role_detail.link(button=SidebarTarget('物品'), destination=page_item_bag)
page_role_detail.link(button=SidebarTarget('挑战'), destination=page_challenge)
page_item_bag.link(button=SidebarTarget('角色'), destination=page_role_detail)
page_item_bag.link(button=G.C_PAGE_PLAYER_BACK, destination=page_main)
page_challenge.link(button=SidebarTarget('角色'), destination=page_role_detail)
page_challenge.link(button=G.C_PAGE_PLAYER_BACK, destination=page_main)

# 录制新页面素材后在这里创建页面对象，Page 的变量名即页面名（由 traceback 反推），
# 命名保持 page_xxx 格式，例如：
#   page_xxx = Page(XxxAssets.I_CHECK_XXX)
#   page_xxx.additional = [XxxAssets.I_AD_CLOSE]
#   page_xxx.link(button=XxxAssets.I_XXX_GOTO_YYY, destination=page_yyy)
# 每个任务也可以在自己的 tasks/<Task>/page.py 中扩展页面（GameUi 启动时会自动加载）。


def random_click(low: int = None, high: int = None, ltrb: tuple = (True, False, True, False)) -> RuleClick | list[RuleClick]:
    """
    随机生成RuleClick, 不传入参数则返回1个RuleClick, 传入参数则生成范围内的click数组
    :return: RuleClick或者RuleClick的数组
    """
    from tasks.Component.GeneralBattle.assets import GeneralBattleAssets as GBA
    click_area_list = [GBA.C_REWARD_1, GBA.C_REWARD_2, GBA.C_REWARD_3]
    click = random.choice(list(compress(click_area_list, ltrb)))
    click.name = "SAFE_RANDOM_CLICK"
    if low is None or high is None:
        return click
    return [click for _ in range(random.randint(low, high))]
