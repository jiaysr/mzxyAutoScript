# This Python file uses the following encoding: utf-8
"""
战斗界面的技能位坐标（1280x720，真机实测）

单独放一个纯数据模块，是为了让「不碰设备的代码」也能引用坐标：
后台 API（`module/server/battle_router.py`）只想给前端返回技能位信息，
不应该为了几个坐标去 import 整个设备/任务栈。

坐标来源（2026-10-23 真机标定，迷窟里逐个吻合）：
- 点击区域：按钮中心偏右下，点这里不会点到按钮边缘的冷却数字；
- 判定区域：以按钮为中心的 52x54 方框，技能图标识别和冷却识别共用。
"""
from typing import Dict, Tuple

# 点击区域 (x, y, w, h)，7 个技能位
BATTLE_SKILL_REGIONS: Tuple[Tuple[int, int, int, int], ...] = (
    (995, 588, 47, 42),    # 技能位1
    (1034, 490, 50, 41),   # 技能位2
    (1125, 442, 53, 44),   # 技能位3
    (887, 587, 55, 44),    # 技能位4
    (923, 483, 46, 46),    # 技能位5
    (998, 391, 48, 45),    # 技能位6
    (1092, 337, 56, 42),   # 技能位7
)

# 判定区域 (x, y, w, h)：图标识别 + 冷却识别共用，与点击区域同心
BATTLE_SKILL_CHECK_REGIONS: Dict[int, Tuple[int, int, int, int]] = {
    0: (991, 582, 52, 54),
    1: (1033, 484, 52, 54),
    2: (1125, 437, 52, 54),
    3: (888, 582, 52, 54),
    4: (920, 479, 52, 54),
    5: (996, 386, 52, 54),
    6: (1094, 331, 52, 54),
}

# 普通攻击按钮区域
BATTLE_ATTACK_REGION: Tuple[int, int, int, int] = (1155, 605, 64, 46)

# 技能图标在判定区域里的位置 (x, y, w, h)：只取按钮中间的图标本体，
# 去掉外圈金属边框和高光 —— 边框所有技能都一样，带着它算相似度会把不相干的技能拉得很像
SKILL_ICON_INSET: Tuple[int, int, int, int] = (6, 7, 40, 40)
# 图标素材尺寸
SKILL_ICON_SIZE: Tuple[int, int] = (SKILL_ICON_INSET[2], SKILL_ICON_INSET[3])

# 技能位数量
SLOT_COUNT = len(BATTLE_SKILL_REGIONS)


def skill_icon_region(index: int) -> Tuple[int, int, int, int]:
    """某个技能位里「图标本体」的绝对区域 (x, y, w, h)"""
    x, y, _, _ = BATTLE_SKILL_CHECK_REGIONS[index]
    ix, iy, iw, ih = SKILL_ICON_INSET
    return x + ix, y + iy, iw, ih


def slot_name(index: int) -> str:
    """技能位显示名：技能1 ~ 技能7（下标从 0 开始）"""
    return f'技能{index + 1}'


def slots_info() -> list:
    """给前端/日志用的技能位信息"""
    return [{'index': index, 'name': slot_name(index),
             'region': list(BATTLE_SKILL_CHECK_REGIONS[index]),
             'click': list(BATTLE_SKILL_REGIONS[index])}
            for index in range(SLOT_COUNT)]
