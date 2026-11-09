# -*- coding: utf-8 -*-
"""每日签到任务框架测试（不连设备）"""
from tasks.DailySignIn.config import DailySignIn


def test_default_flags_are_enabled():
    cfg = DailySignIn().daily_sign_in_config
    assert cfg.xiuxin is True
    assert cfg.claim_vip_gift is True
    assert cfg.claim_salary is True
    assert cfg.claim_zodiac_gift is True


def test_flags_can_be_disabled():
    cfg = DailySignIn(**{'daily_sign_in_config': {
        'xiuxin': False,
        'claim_vip_gift': False,
        'claim_salary': False,
        'claim_zodiac_gift': False,
    }}).daily_sign_in_config
    assert cfg.xiuxin is False
    assert cfg.claim_vip_gift is False
    assert cfg.claim_salary is False
    assert cfg.claim_zodiac_gift is False


def test_script_task_uses_task_name():
    from tasks.DailySignIn.script_task import ScriptTask
    assert ScriptTask.SCHEDULER_NAME == 'DailySignIn'
