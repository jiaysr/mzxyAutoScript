# This Python file uses the following encoding: utf-8
"""兼容层：冷却判定的实现已移到 tasks/Battle/cooldown.py（战斗模块内，纯逻辑不碰设备）

保留这个文件是为了不破坏旧引用（测试、监控脚本、任务里都还在从这里 import）。
新代码建议直接 `from tasks.Battle.cooldown import ...`。
"""
from tasks.Battle.cooldown import (SKILL_COOLDOWN_RATIO, SKILL_COOLING, SKILL_READY,
                                   SKILL_SCORE_RADIUS, SKILL_UNKNOWN, SkillCooldownJudge,
                                   disc_mask, skill_region_score)

__all__ = ['SKILL_COOLDOWN_RATIO', 'SKILL_COOLING', 'SKILL_READY', 'SKILL_SCORE_RADIUS',
           'SKILL_UNKNOWN', 'SkillCooldownJudge', 'disc_mask', 'skill_region_score']
