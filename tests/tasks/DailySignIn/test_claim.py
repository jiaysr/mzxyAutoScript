# -*- coding: utf-8 -*-
"""每日签到-生肖福利礼包/俸禄流程测试（不连设备）"""
from types import SimpleNamespace

import numpy as np

from tasks.DailySignIn.script_task import ScriptTask


class FakeZodiacTask:
    I_ZODIAC_GIFT_CLAIMABLE = 'claimable'
    I_ZODIAC_GIFT_CLAIMED = 'claimed'
    I_ZODIAC_GIFT_SUCCESS = 'success'
    I_ZODIAC_GIFT_SUCCESS_CLOSE = 'success_close'

    def __init__(self, claimable=True, success_popup=True):
        self.claimable = claimable
        self.success_popup = success_popup
        self.clicked = []
        self.closed = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None,
                                      click_record_clear=lambda: None)

    def ui_reset_current_page(self):
        pass

    def ui_goto(self, page, timeout=60):
        return True

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        if target == self.I_ZODIAC_GIFT_CLAIMABLE:
            return self.claimable
        if target == self.I_ZODIAC_GIFT_CLAIMED:
            return not self.claimable
        return False

    def appear_then_click(self, target, interval=None):
        self.clicked.append(target)
        return True

    def wait_until_appear(self, target, skip_first_screenshot=False, wait_time=None):
        return self.success_popup and target == self.I_ZODIAC_GIFT_SUCCESS

    def ensure_back_to_main(self):
        self.closed += 1


def test_zodiac_gift_claims_and_closes_success_popup():
    task = FakeZodiacTask(claimable=True, success_popup=True)
    ScriptTask.do_claim_zodiac_gift(task)
    assert task.clicked == [task.I_ZODIAC_GIFT_CLAIMABLE, task.I_ZODIAC_GIFT_SUCCESS_CLOSE]


def test_zodiac_gift_skips_when_already_claimed():
    task = FakeZodiacTask(claimable=False)
    ScriptTask.do_claim_zodiac_gift(task)
    assert task.clicked == []


def test_zodiac_gift_still_closes_when_success_popup_missing():
    task = FakeZodiacTask(claimable=True, success_popup=False)
    ScriptTask.do_claim_zodiac_gift(task)
    assert task.clicked == [task.I_ZODIAC_GIFT_CLAIMABLE]


class FakeVipTask:
    I_VIP_NONE = 'vip_none'
    I_VIP_WELFARE_CLAIMED = 'welfare_claimed'
    I_VIP_WELFARE_CLAIMABLE = 'welfare_claimable'
    I_VIP_WELFARE_SUCCESS = 'welfare_success'
    I_VIP_WELFARE_SUCCESS_CLOSE = 'welfare_success_close'

    def __init__(self, no_vip=False, claimed=False, success_popup=True):
        self.no_vip = no_vip
        self.claimed = claimed
        self.success_popup = success_popup
        self.pages = []
        self.clicked = []
        self.closed = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None,
                                      click_record_clear=lambda: None)

    def ui_reset_current_page(self):
        pass

    def ui_goto(self, page, timeout=60):
        self.pages.append(page.name if hasattr(page, 'name') else page)
        return True

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        if target == self.I_VIP_NONE:
            return self.no_vip
        if target == self.I_VIP_WELFARE_CLAIMED:
            return self.claimed
        if target == self.I_VIP_WELFARE_CLAIMABLE:
            return not self.claimed
        return False

    def appear_then_click(self, target, interval=None):
        self.clicked.append(target)
        return True

    def wait_until_appear(self, target, skip_first_screenshot=False, wait_time=None):
        return self.success_popup and target == self.I_VIP_WELFARE_SUCCESS

    def ensure_back_to_main(self):
        self.closed += 1


def test_vip_gift_skips_without_vip():
    from tasks.DailySignIn.page import page_vip
    task = FakeVipTask(no_vip=True)
    ScriptTask.do_claim_vip_gift(task)
    assert task.pages == [page_vip.name]
    assert task.clicked == []
    assert task.closed == 1


def test_vip_gift_claims_and_closes_back_to_vip():
    from tasks.DailySignIn.page import page_vip, page_vip_welfare
    task = FakeVipTask(claimed=False, success_popup=True)
    ScriptTask.do_claim_vip_gift(task)
    assert task.pages == [page_vip.name, page_vip_welfare.name, page_vip.name]
    assert task.clicked == [task.I_VIP_WELFARE_CLAIMABLE,
                            task.I_VIP_WELFARE_SUCCESS_CLOSE]
    assert task.closed == 1


def test_vip_gift_skips_when_already_claimed():
    from tasks.DailySignIn.page import page_vip, page_vip_welfare
    task = FakeVipTask(claimed=True)
    ScriptTask.do_claim_vip_gift(task)
    assert task.pages == [page_vip.name, page_vip_welfare.name, page_vip.name]
    assert task.clicked == []
    assert task.closed == 1

class FakeSalaryTask:
    SALARY_MAX_CLICKS = 3
    SALARY_LIT_SATURATION = 120
    I_XIANMENG_INFO_PAGE = 'info_page'
    I_XIANMENG_NONE = 'no_guild'
    C_CLAIM_SALARY = object()

    def __init__(self, lit=True, grays_after_clicks=1, no_guild=False,
                 info_page_visible=True, popup_visible=False):
        self.lit = lit
        self.grays_after_clicks = grays_after_clicks
        self.no_guild = no_guild
        self.info_page_visible = info_page_visible
        # 是否有挡在信息页上的弹窗（能在 close_salary_popup 里关掉）
        self.popup_visible = popup_visible
        self.clicks = 0
        self.popups_closed = 0
        self.closed = 0
        self.pages = []
        self.device = SimpleNamespace(sleep=lambda seconds: None,
                                      click_record_clear=lambda: None)

    def ui_reset_current_page(self):
        pass

    def ui_goto(self, page, timeout=60):
        self.pages.append(page.name if hasattr(page, 'name') else page)
        return True

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        if target == self.I_XIANMENG_INFO_PAGE:
            return self.info_page_visible
        if target == self.I_XIANMENG_NONE:
            return self.no_guild
        return False

    def click(self, target, interval=None):
        self.clicks += 1
        if self.clicks >= self.grays_after_clicks:
            self.lit = False
        return True

    def salary_button_lit(self):
        return self.lit

    def wait_salary_claimed(self, timeout=4):
        return not self.lit

    def close_salary_popup(self):
        self.popups_closed += 1
        return self.popup_visible

    def ensure_back_to_main(self):
        self.closed += 1


class FakeImageTask:
    """只测 salary_button_lit 用：给一张合成画面"""

    SALARY_LIT_SATURATION = 120
    salary_saturation = ScriptTask.salary_saturation

    def __init__(self, image, roi_front=(10, 10, 20, 20)):
        self.device = SimpleNamespace(image=image, sleep=lambda seconds: None)
        self.C_CLAIM_SALARY = SimpleNamespace(roi_front=roi_front)


def test_salary_skips_when_not_in_guild():
    from tasks.DailySignIn.page import page_xianmeng_member
    task = FakeSalaryTask(no_guild=True)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == 0
    assert task.pages == [page_xianmeng_member.name]


def test_salary_skips_click_when_already_gray():
    from tasks.DailySignIn.page import page_xianmeng_info
    task = FakeSalaryTask(lit=False)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == 0
    assert task.pages[-1] == page_xianmeng_info.name


def test_salary_clicks_once_when_button_turns_gray():
    task = FakeSalaryTask(lit=True)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == 1


def test_salary_stops_after_max_clicks():
    task = FakeSalaryTask(lit=True, grays_after_clicks=999)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == task.SALARY_MAX_CLICKS


def test_salary_reenters_info_page_when_panel_closed():
    from tasks.DailySignIn.page import page_xianmeng_info
    task = FakeSalaryTask(lit=True, info_page_visible=False)
    ScriptTask.do_claim_salary(task)
    assert task.pages.count(page_xianmeng_info.name) >= task.SALARY_MAX_CLICKS
    assert task.clicks == 0


def test_salary_closes_popup_instead_of_reentering():
    from tasks.DailySignIn.page import page_xianmeng_info
    task = FakeSalaryTask(lit=True, info_page_visible=False, popup_visible=True)
    ScriptTask.do_claim_salary(task)
    # 弹窗能关就一直关，不必反复重新导航
    assert task.popups_closed >= 1
    assert task.pages.count(page_xianmeng_info.name) == 1
    assert task.clicks == 0


def test_salary_button_lit_uses_saturation_not_template():
    """点亮按钮是蓝色（高饱和），已领取是灰色 —— 只有颜色能区分两种状态"""
    lit = np.zeros((40, 40, 3), dtype=np.uint8)
    lit[10:30, 10:30] = (20, 55, 110)          # 蓝色按钮
    gray = np.zeros((40, 40, 3), dtype=np.uint8)
    gray[10:30, 10:30] = (62, 64, 62)          # 灰色按钮

    assert ScriptTask.salary_button_lit(FakeImageTask(lit)) is True
    assert ScriptTask.salary_button_lit(FakeImageTask(gray)) is False
    assert ScriptTask.salary_button_lit(FakeImageTask(None)) is False
