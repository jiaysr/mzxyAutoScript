# -*- coding: utf-8 -*-
"""种植-页面注册与导航测试（不连设备）"""
import numpy as np

from tasks.GameUi.navigation.panel import PanelNavigation
from tasks.GameUi.pages.page import PageRegistry
from tasks.GameUi.pages.targets import TabTarget
from tasks.ZhongZhi.assets import ZhongZhiAssets as Z
from tasks.ZhongZhi.page import page_zhongzhi
from tasks.DailySignIn.page import page_fuhua


def test_zhongzhi_page_registered_for_auxiliary_module():
    assert page_zhongzhi in PageRegistry.all()
    assert page_zhongzhi.module == '辅助'
    assert page_zhongzhi.check_button == [Z.I_ZHONGZHI_PAGE, Z.I_ZHONGZHI_HELP]


def test_fuhua_links_to_zhongzhi_tab_and_back():
    target = page_fuhua.links.get(page_zhongzhi)
    assert isinstance(target, TabTarget)
    assert target.name == '种植'
    assert target.module == '辅助'

    back = page_zhongzhi.links.get(page_fuhua)
    assert isinstance(back, TabTarget)
    assert back.name == '幻化'
    assert back.module == '辅助'


def test_auxiliary_tabs_include_mohua_before_zhongzhi():
    tabs = PanelNavigation.PANEL_TABS['辅助']
    assert tabs == ['幻化', '金身', '坐骑', '生肖', '魔画', '种植']


def test_zhongzhi_page_feature_matches_screenshot_crop():
    # 把素材贴到页面特征所在位置，页面识别应命中
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    x, y, w, h = Z.I_ZHONGZHI_PAGE.roi_front
    image[y:y + h, x:x + w] = Z.I_ZHONGZHI_PAGE.image
    assert Z.I_ZHONGZHI_PAGE.match(image) is True
