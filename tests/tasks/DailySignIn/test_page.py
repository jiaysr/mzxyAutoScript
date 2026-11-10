# -*- coding: utf-8 -*-
"""每日签到-奖励弹窗页面注册回归测试（不连设备）"""
from tasks.DailySignIn.assets import DailySignInAssets as D
from tasks.DailySignIn.page import (REWARD_TAB_PAGES, RewardTabTarget,
                                    page_fuhua, page_reward,
                                    page_reward_online, page_reward_sign_in,
                                    page_shengxiao, page_shengxiao_shop,
                                    page_vip, page_vip_welfare,
                                    page_xianmeng_info, page_xianmeng_member,
                                    page_xiuxin)
from tasks.GameUi.assets import GameUiAssets as G
from tasks.GameUi.core.engine import NavigationEngine
from tasks.GameUi.pages.page import (PageRegistry, page_activity,
                                     page_main, page_role_detail)
from tasks.GameUi.pages.targets import MenuTarget, TabTarget


def test_reward_pages_registered_before_activity_and_main():
    names = [page.name for page in PageRegistry.all()]
    sign_in = names.index(page_reward_sign_in.name)
    container = names.index(page_reward.name)
    # 具体 tab 页在前、容器页在后；都必须排在活动弹窗/主页面之前，
    # 否则奖励弹窗会被误判成活动弹窗或主页面
    assert sign_in < container
    assert container < names.index(page_activity.name)
    assert container < names.index(page_main.name)


def test_main_menu_opens_reward_popup():
    target = page_main.links.get(page_reward)
    assert isinstance(target, MenuTarget)
    assert target.icon is D.I_REWARD_MENU_ICON


def test_reward_container_links_to_each_tab():
    for tab_name, page in REWARD_TAB_PAGES.items():
        target = page_reward.links.get(page)
        assert isinstance(target, RewardTabTarget)
        assert target.name == tab_name


def test_reward_tabs_can_switch_to_each_other_and_close():
    for tab_name, page in REWARD_TAB_PAGES.items():
        assert page.links.get(page_main) is G.I_CLOSE_X_GREEN
        for other_name, other_page in REWARD_TAB_PAGES.items():
            if other_page is page:
                continue
            target = page.links.get(other_page)
            assert isinstance(target, RewardTabTarget)
            assert target.name == other_name


class _FakePageMatchTask:
    """只命中指定特征，其余都不命中"""

    def __init__(self, hit):
        self.hit = hit

    def maybe_screenshot(self, skip_first_screenshot):
        pass

    def appear(self, target, interval=None):
        return target is self.hit


def test_online_page_matches_any_of_three_features():
    # 在线领奖页有 3 个特征点，任意一个命中就应识别为在线领奖页
    assert page_reward_online.check_button == [
        D.I_REWARD_ONLINE_1,
        D.I_REWARD_ONLINE_2,
        D.I_REWARD_ONLINE_3,
    ]
    for hit in (D.I_REWARD_ONLINE_1, D.I_REWARD_ONLINE_2, D.I_REWARD_ONLINE_3):
        fake = _FakePageMatchTask(hit)
        assert NavigationEngine.ui_page_appear(fake, page_reward_online) is True


def test_online_page_not_matched_when_no_feature_hits():
    fake = _FakePageMatchTask(object())
    assert NavigationEngine.ui_page_appear(fake, page_reward_online) is False


def test_xiuxin_page_registered_before_main():
    names = [page.name for page in PageRegistry.all()]
    assert names.index(page_xiuxin.name) < names.index(page_main.name)


def test_xiuxin_page_links():
    assert page_role_detail.links.get(page_xiuxin) is D.I_XIUXIN_ENTRY
    assert page_xiuxin.links.get(page_role_detail) is G.C_PAGE_PLAYER_BACK


def test_shengxiao_pages_and_shop_links():
    # 辅助模块首 tab = 幻化，生肖是第二个 tab
    assert page_fuhua.module == '辅助' and page_fuhua.module_home is True
    assert page_shengxiao.module == '辅助'
    fuhua_to_shengxiao = page_fuhua.links.get(page_shengxiao)
    assert isinstance(fuhua_to_shengxiao, TabTarget)
    assert fuhua_to_shengxiao.name == '生肖'

    assert page_shengxiao.links.get(page_shengxiao_shop) is D.I_SHENGXIAO_SHOP_BUTTON
    assert page_shengxiao_shop.links.get(page_shengxiao) is D.I_SHENGXIAO_SHOP_CLOSE


def test_xianmeng_pages_links():
    assert page_xianmeng_member.module == '仙盟'
    assert page_xianmeng_member.module_home is True
    assert page_xianmeng_info.module == '仙盟'
    target = page_xianmeng_member.links.get(page_xianmeng_info)
    assert isinstance(target, TabTarget)
    assert target.name == '信息'


def test_xianmeng_member_page_accepts_no_guild():
    assert D.I_XIANMENG_NONE in page_xianmeng_member.check_button


def test_shengxiao_shop_registered_before_main():
    names = [page.name for page in PageRegistry.all()]
    assert names.index(page_shengxiao_shop.name) < names.index(page_main.name)


def test_vip_pages_and_links():
    assert page_main.links.get(page_vip) is D.C_VIP_ENTRY
    assert page_vip.links.get(page_vip_welfare) is D.I_VIP_WELFARE_ENTRY
    # 关闭每日福利页回 VIP 页，而不是直接回主页面
    assert page_vip_welfare.links.get(page_vip) is D.I_VIP_CLOSE
    assert page_vip.links.get(page_main) is D.I_VIP_CLOSE


def test_vip_pages_registered_before_main():
    names = [page.name for page in PageRegistry.all()]
    assert names.index(page_vip.name) < names.index(page_main.name)
    assert names.index(page_vip_welfare.name) < names.index(page_main.name)
