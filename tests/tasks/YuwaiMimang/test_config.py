# -*- coding: utf-8 -*-
"""域外迷窟 配置测试（不连设备）"""
from tasks.YuwaiMimang.config import YuwaiMimang


def test_battle_config_has_no_fast_relock():
    cfg = YuwaiMimang()
    assert cfg.battle_config.target_names == '小鬼,鬼王,鬼将'
    assert not hasattr(cfg.battle_config, 'fast_relock')
