# This Python file uses the following encoding: utf-8
"""
右下角动作按钮识别（纯逻辑，不碰设备）

锁定目标后，右下角按钮的盘面颜色直接表明当前能做什么：
- 红盘「攻击」-> 可攻击
- 绿盘「采集」-> 不可攻击（任务采集物）
- 蓝盘「交互」-> 不可攻击（玩家 / NPC）

位置与盘面颜色在 1280x720 下固定，用圆内饱和像素的中位色相分档：
比模板匹配快 15~60 倍（实测 0.04ms vs 0.55ms），且一次就能分出三种状态。
真机样本（2026-10，16 张锁定截图）实测：攻击 H≈7、采集 H≈52、交互 H≈96~104，
组间无重叠；菜单/弹窗里按钮不显示时饱和像素不足，按「不可见」处理。
"""
from typing import Optional

import cv2
import numpy as np

from tasks.Battle.slots import ACTION_BUTTON_DISC_CENTER, ACTION_BUTTON_DISC_RADIUS

ACTION_ATTACK = 'attack'
ACTION_GATHER = 'gather'
ACTION_INTERACT = 'interact'
ACTION_OTHER = 'other'

ACTION_NAMES_ZH = {
    ACTION_ATTACK: '攻击',
    ACTION_GATHER: '采集',
    ACTION_INTERACT: '交互',
    ACTION_OTHER: '其他',
}

# 圆内至少要有这么多饱和像素才认为按钮可见（菜单/弹窗里按钮不显示时远低于此值）
MIN_SATURATED_PIXELS = 400
# 只取盘面填充：银龙浮雕饱和度低、黄/青文字后面会被色相分档排除
MIN_SATURATION = 120
MIN_VALUE = 90


def classify_hue(hue: int) -> str:
    """OpenCV 色相（0~179）-> 动作类别"""
    if hue < 15 or hue >= 165:
        return ACTION_ATTACK
    if hue < 45:
        return ACTION_OTHER
    if hue < 90:
        return ACTION_GATHER
    if hue < 135:
        return ACTION_INTERACT
    return ACTION_OTHER


def classify_action_button(image, center: tuple = None, radius: int = None) -> Optional[str]:
    """
    识别右下角动作按钮的类别

    :param image: RGB numpy 数组（与 device.image 同格式）
    :param center: 圆盘中心，默认 (1184, 622)
    :param radius: 内圈采样半径，默认 32
    :return: 'attack' / 'gather' / 'interact' / 'other'；按钮不可见返回 None
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    height, width = image.shape[:2]
    cx, cy = center or ACTION_BUTTON_DISC_CENTER
    r = int(radius or ACTION_BUTTON_DISC_RADIUS)
    x0, y0 = max(0, cx - r), max(0, cy - r)
    x1, y1 = min(width, cx + r + 1), min(height, cy + r + 1)
    roi = image[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    disc = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r
    saturated = disc & (sat > MIN_SATURATION) & (val > MIN_VALUE)
    if int(saturated.sum()) < MIN_SATURATED_PIXELS:
        return None
    return classify_hue(int(np.median(hue[saturated])))


def is_attackable(action: Optional[str]) -> bool:
    """动作按钮为「攻击」才算可攻击"""
    return action == ACTION_ATTACK
