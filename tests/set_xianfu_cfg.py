# This Python file uses the following encoding: utf-8
"""临时：设置仙府九重天配置并保存（验证配置注册是否成功）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config

c = Config('oas1')
cfg = c.xianfu_jiuchongtian.xianfu_config
print('before:', cfg.stage, cfg.stone_threshold, cfg.max_battles,
      cfg.click_empty_skills, cfg.target_coord)
cfg.stage = 1                 # 阶段1 = 打各小关1（南极）
cfg.stone_threshold = 40      # 仙石满 40 才开始清
cfg.max_battles = 0           # 不限局数，一轮清到仙石不够
cfg.click_empty_skills = False  # 不点空技能位
cfg.target_coord = '210,158'
c.save()
print('after :', c.xianfu_jiuchongtian.xianfu_config.stage,
      c.xianfu_jiuchongtian.xianfu_config.stone_threshold,
      c.xianfu_jiuchongtian.xianfu_config.max_battles,
      c.xianfu_jiuchongtian.xianfu_config.click_empty_skills)
