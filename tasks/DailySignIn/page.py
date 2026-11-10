# This Python file uses the following encoding: utf-8
"""
每日签到-奖励弹窗页面定义

奖励弹窗有 5 个 tab（可横向滚动）：
    至臻礼包 / 签到板 / 在线领奖 / 兑换礼包 / 绑定手机

签到任务本体只用到「签到板」，其余 tab 也一并注册，方便以后扩展。
"""
from tasks.DailySignIn.assets import DailySignInAssets as D
from tasks.GameUi.assets import GameUiAssets as G
from tasks.GameUi.pages.page import (Page, PageRegistry, page_main,
                                     page_role_detail)
from tasks.GameUi.pages.targets import MenuTarget, TabTarget


class RewardTabTarget:
    """奖励弹窗顶部 tab（OCR 定位，支持左右滚动查找）"""

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return f'reward_tab[{self.name}]'


# tab 顺序（用于判断左右滚动方向）
REWARD_TABS = ('至臻礼包', '签到板', '在线领奖', '兑换礼包', '绑定手机')

# 具体 tab 页面：用各 tab 的内容特征识别，切 tab 后不会误判
page_reward_top_gift = Page(D.I_REWARD_TOP_GIFT)          # 至臻礼包
page_reward_sign_in = Page(D.I_REWARD_SIGN_IN)            # 签到板
page_reward_online = Page([D.I_REWARD_ONLINE_1,           # 在线领奖
                           D.I_REWARD_ONLINE_2,
                           D.I_REWARD_ONLINE_3])
page_reward_exchange = Page(D.I_REWARD_EXCHANGE)          # 兑换礼包
page_reward_bind_phone = Page(D.I_REWARD_BIND_PHONE)      # 绑定手机

# 奖励弹窗容器页：任意 tab 的内容特征命中即认为弹窗打开
page_reward = Page([
    D.I_REWARD_TOP_GIFT,
    D.I_REWARD_SIGN_IN,
    D.I_REWARD_ONLINE_1,
    D.I_REWARD_ONLINE_2,
    D.I_REWARD_ONLINE_3,
    D.I_REWARD_EXCHANGE,
    D.I_REWARD_BIND_PHONE,
])

REWARD_TAB_PAGES = {
    '至臻礼包': page_reward_top_gift,
    '签到板': page_reward_sign_in,
    '在线领奖': page_reward_online,
    '兑换礼包': page_reward_exchange,
    '绑定手机': page_reward_bind_phone,
}


# 主页面菜单展开后点「奖励」图标 -> 奖励弹窗
page_main.link(button=MenuTarget(D.I_REWARD_MENU_ICON), destination=page_reward)

# 弹窗内：任意 tab 都能切到其他 tab，右上角绿 X 关闭回主页面
for _tab_name, _page in REWARD_TAB_PAGES.items():
    page_reward.link(button=RewardTabTarget(_tab_name), destination=_page)
    _page.link(button=G.I_CLOSE_X_GREEN, destination=page_main)
    for _other_name, _other_page in REWARD_TAB_PAGES.items():
        if _other_page is _page:
            continue
        _page.link(button=RewardTabTarget(_other_name), destination=_other_page)

page_reward.link(button=G.I_CLOSE_X_GREEN, destination=page_main)

# 修心页面：角色-详情 -> 修心 -> 修心页面；右上角返回回角色详情
page_xiuxin = Page(D.I_XIUXIN_PAGE)
page_role_detail.link(button=D.I_XIUXIN_ENTRY, destination=page_xiuxin)
page_xiuxin.link(button=G.C_PAGE_PLAYER_BACK, destination=page_role_detail)

# 辅助模块：幻化（首 tab）<-> 生肖；生肖页右下角「商店」打开生肖商店
page_fuhua = Page(D.I_FUHUA_PAGE)
page_fuhua.module = '辅助'
page_fuhua.module_home = True
page_shengxiao = Page(D.I_SHENGXIAO_PAGE)
page_shengxiao.module = '辅助'
page_shengxiao_shop = Page(D.I_SHENGXIAO_SHOP_PAGE)
page_fuhua.link(button=TabTarget('生肖', module='辅助'), destination=page_shengxiao)
page_shengxiao.link(button=TabTarget('幻化', module='辅助'), destination=page_fuhua)
page_shengxiao.link(button=D.I_SHENGXIAO_SHOP_BUTTON, destination=page_shengxiao_shop)
page_shengxiao_shop.link(button=D.I_SHENGXIAO_SHOP_CLOSE, destination=page_shengxiao)

# 仙盟模块：成员（首 tab）<-> 信息
# 未加入仙盟时也是「成员」tab 的页面，只是中间换成未加入提示；
# 两个特征任意一个命中都识别为 page_xianmeng_member
page_xianmeng_member = Page([D.I_XIANMENG_MEMBER_PAGE, D.I_XIANMENG_NONE])
page_xianmeng_member.module = '仙盟'
page_xianmeng_member.module_home = True
page_xianmeng_info = Page(D.I_XIANMENG_INFO_PAGE)
page_xianmeng_info.module = '仙盟'
page_xianmeng_member.link(button=TabTarget('信息', module='仙盟'), destination=page_xianmeng_info)
page_xianmeng_info.link(button=TabTarget('成员', module='仙盟'), destination=page_xianmeng_member)

# VIP：主页面入口 -> VIP页面 -> 每日福利页；关闭每日福利回 VIP 页，不直接关 VIP
page_vip = Page(D.I_VIP_PAGE)
page_vip_welfare = Page(D.I_VIP_WELFARE_PAGE)
page_main.link(button=D.C_VIP_ENTRY, destination=page_vip)
page_vip.link(button=D.I_VIP_WELFARE_ENTRY, destination=page_vip_welfare)
page_vip_welfare.link(button=D.I_VIP_CLOSE, destination=page_vip)
page_vip.link(button=D.I_VIP_CLOSE, destination=page_main)

# 奖励弹窗/修心页面打开时，主页面右下角和活动弹窗的特征仍然会命中
# （奖励弹窗实测匹配 1.0），而任务 page.py 是运行时才导入的，
# 天然排在基础页面后面。这里把这些任务页面插到注册表最前面，
# 避免 ui_get_current_page 先把弹窗/子页误判成活动弹窗或主页面。
# 奖励弹窗内部：具体 tab 页在前，容器页在后。
_front_pages = [
    page_reward_top_gift,
    page_reward_sign_in,
    page_reward_online,
    page_reward_exchange,
    page_reward_bind_phone,
    page_reward,
    page_xiuxin,
    page_shengxiao_shop,
    page_vip,
    page_vip_welfare,
]
_registry = PageRegistry._registry
for _page in _front_pages:
    if _page in _registry:
        _registry.remove(_page)
_registry[0:0] = _front_pages
