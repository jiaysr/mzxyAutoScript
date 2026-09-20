# This Python file uses the following encoding: utf-8
from itertools import compress

import random

import traceback
from module.atom.click import RuleClick
from tasks.GameUi.assets import GameUiAssets as G
from tasks.GameUi.targets import SidebarTarget, MenuTarget, ActivityTabTarget, ActivitySubTabTarget


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


# ************************************* 明珠轩辕页面注册区 *****************************************#
# 活动弹窗：具体页在前、容器页在后（都要放在 page_main 之前：弹窗打开时主页面特征仍会命中）
page_activity_list = Page(G.I_PAGE_ACTIVITY_LIST)              # 活动-推荐（顶部"活动"tab 的默认子 tab）
page_activity_world_boss = Page(G.I_PAGE_ACTIVITY_WORLD_BOSS)  # 活动-世界首领
page_activity_notice = Page(G.I_PAGE_ACTIVITY_NOTICE)          # 活动-公告
page_activity_active = Page(G.I_PAGE_ACTIVITY_ACTIVE)          # 活动-活跃
page_activity = Page(G.I_PAGE_ACTIVITY)                        # 活动弹窗容器（右上角绿X，兜底）
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
page_main.link(button=MenuTarget(G.I_ACTIVITY_ICON), destination=page_activity)
# 活动弹窗内部：顶部 tab 从容器页进入（切"活动"tab 会重置到推荐子 tab）
page_activity.link(button=ActivityTabTarget('公告'), destination=page_activity_notice)
page_activity.link(button=ActivityTabTarget('活动'), destination=page_activity_list)
page_activity.link(button=ActivityTabTarget('活跃'), destination=page_activity_active)
page_activity_notice.link(button=ActivityTabTarget('活动'), destination=page_activity_list)
page_activity_list.link(button=ActivityTabTarget('公告'), destination=page_activity_notice)
page_activity_active.link(button=ActivityTabTarget('公告'), destination=page_activity_notice)
# 活动弹窗内部：右侧子 tab（可上下滚动）
page_activity_list.link(button=ActivitySubTabTarget('世界首领'), destination=page_activity_world_boss)
page_activity_world_boss.link(button=ActivitySubTabTarget('推荐'), destination=page_activity_list)
# 关闭弹窗回主页面
page_activity.link(button=G.C_ACTIVITY_CLOSE, destination=page_main)
page_activity_notice.link(button=G.C_ACTIVITY_CLOSE, destination=page_main)
page_activity_list.link(button=G.C_ACTIVITY_CLOSE, destination=page_main)
page_activity_world_boss.link(button=G.C_ACTIVITY_CLOSE, destination=page_main)
page_activity_active.link(button=G.C_ACTIVITY_CLOSE, destination=page_main)
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
