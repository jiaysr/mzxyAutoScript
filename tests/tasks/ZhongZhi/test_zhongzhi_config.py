# -*- coding: utf-8 -*-
"""种植-配置测试（不连设备）"""
from module.config.config_model import ConfigModel
from tasks.ZhongZhi.config import ZhongZhi


def test_default_scheduler_is_disabled():
    cfg = ZhongZhi()
    assert cfg.scheduler.enable is False


def test_script_task_uses_task_name():
    from tasks.ZhongZhi.script_task import ScriptTask
    assert ScriptTask.SCHEDULER_NAME == 'ZhongZhi'


def test_config_model_registers_zhong_zhi():
    model = ConfigModel()
    assert isinstance(model.zhong_zhi, ZhongZhi)
