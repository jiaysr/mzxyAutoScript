# -*- coding: utf-8 -*-
"""限时活动-野外雪人 配置/菜单测试（不连设备）"""
from module.config.config_manual import ConfigManual
from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel
from tasks.GameUi.core.utils import UiUtils
from tasks.LimitedTime.WildSnowman.config import WildSnowman


def test_default_config():
    cfg = WildSnowman()
    snowman = cfg.wild_snowman_config
    assert snowman.battle_coord == '251,175'
    assert snowman.battle_tolerance == 5
    assert snowman.heal_repair_interval_minutes == 40
    assert '陵鱼' in snowman.target_names
    assert '陵魚' in snowman.target_names
    assert '可爱雪人' in snowman.target_names
    assert '可愛雪人' in snowman.target_names
    assert cfg.battle_config.fast_relock is True
    assert cfg.scheduler.enable is False
    assert cfg.scheduler.priority == 100


def test_config_model_registers_wild_snowman():
    model = ConfigModel()
    assert isinstance(model.wild_snowman, WildSnowman)


def test_menu_has_limited_time_group():
    menu = ConfigMenu().gui_menu_list
    assert menu['限时活动'] == ['WildSnowman']


def test_scheduler_priority_is_last():
    names = [name.strip() for name in ConfigManual.SCHEDULER_PRIORITY.split('>')
             if name.strip()]
    assert names[-1] == 'WildSnowman'
    assert 'AutoTask' in names
    assert names.index('WildSnowman') > names.index('AutoTask')


def test_higher_priority_names_exclude_self():
    class FakeWildSnowman(UiUtils):
        SCHEDULER_NAME = 'WildSnowman'

    names = FakeWildSnowman.higher_priority_task_names()
    assert 'WildSnowman' not in names
    assert 'Restart' in names
    assert 'AutoTask' in names
    assert names[-1] == 'MikuXianjing'
