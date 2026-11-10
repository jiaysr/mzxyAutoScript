# -*- coding: utf-8 -*-
"""每日签到-生肖福利礼包/俸禄流程测试（不连设备）"""
from types import SimpleNamespace

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
    I_SALARY_UNAVAILABLE = 'unavailable'
    I_XIANMENG_INFO_PAGE = 'info_page'
    I_XIANMENG_NONE = 'no_guild'
    C_CLAIM_SALARY = object()

    def __init__(self, unavailable_after_screens, no_guild=False):
        self.unavailable_after_screens = unavailable_after_screens
        self.no_guild = no_guild
        self.screens = 0
        self.clicks = 0
        self.unknown_closed = 0
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
        self.screens += 1

    def appear(self, target, interval=None):
        if target == self.I_SALARY_UNAVAILABLE:
            return self.screens >= self.unavailable_after_screens
        if target == self.I_XIANMENG_INFO_PAGE:
            return True
        if target == self.I_XIANMENG_NONE:
            return self.no_guild
        return False

    def click(self, target, interval=None):
        self.clicks += 1
        return True

    def try_close_unknown_page(self, skip_screenshot=True):
        self.unknown_closed += 1
        return True

    def ensure_back_to_main(self):
        self.closed += 1


def test_salary_skips_when_not_in_guild():
    from tasks.DailySignIn.page import page_xianmeng_member
    task = FakeSalaryTask(unavailable_after_screens=1, no_guild=True)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == 0
    assert task.pages == [page_xianmeng_member.name]


def test_salary_skips_click_when_unavailable():
    task = FakeSalaryTask(unavailable_after_screens=1)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == 0


def test_salary_clicks_until_unavailable():
    # 多了一次「未加入仙盟」检查的截图，所以第 2 次循环截图时已经变成不可领取
    task = FakeSalaryTask(unavailable_after_screens=3)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == 1
    assert task.unknown_closed == 1


def test_salary_stops_after_max_clicks():
    task = FakeSalaryTask(unavailable_after_screens=999)
    ScriptTask.do_claim_salary(task)
    assert task.clicks == task.SALARY_MAX_CLICKS
    assert task.unknown_closed == task.SALARY_MAX_CLICKS
