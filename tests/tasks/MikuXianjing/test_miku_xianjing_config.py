# -*- coding: utf-8 -*-
"""迷窟仙境 配置 / 注册测试（不连设备）"""
from module.config.config_manual import ConfigManual
from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel
from tasks.MikuXianjing.config import MikuXianjing
from tasks.MikuXianjing.script_task import ScriptTask


def test_config_defaults():
    cfg = MikuXianjing()
    assert cfg.battle_config.target_names == '修仙小妖,修仙刀手'
    assert cfg.battle_config.fast_relock is True
    inner = cfg.miku_xianjing_config
    assert inner.battle_coord == '308,81'
    assert inner.enter_text == '进入迷窟仙境'
    assert inner.npc_name == '迷窟守卫'
    assert inner.relay_location == '沼泽'


def test_model_aggregates_miku_xianjing():
    model = ConfigModel()
    assert isinstance(model.miku_xianjing, MikuXianjing)


def test_menu_in_battle_task():
    menu = ConfigMenu().menu
    assert 'MikuXianjing' in menu['Battle Task']


def test_priority_just_above_wild_snowman():
    names = [n.strip() for n in ConfigManual.SCHEDULER_PRIORITY.split('>') if n.strip()]
    assert names.index('MikuXianjing') < names.index('WildSnowman')
    assert names.index('AutoTask') < names.index('MikuXianjing')


def test_higher_priority_names():
    names = ScriptTask.higher_priority_task_names()
    assert 'MikuXianjing' not in names
    assert 'WildSnowman' not in names
    assert 'AutoTask' in names


def test_script_task_constants():
    assert ScriptTask.SCHEDULER_NAME == 'MikuXianjing'
    assert ScriptTask.MAP_NAME == '迷窟仙境'
    assert ScriptTask.MAP_ENTER_POS == (311, 49)
    assert ScriptTask.LEAVE_EXITS == (
        (715, 151, 31, 32, 'top_right'),
        (377, 168, 31, 32, 'top_right'),
        (270, 540, 31, 32, 'bottom_right'),
    )