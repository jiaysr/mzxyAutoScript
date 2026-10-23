# This Python file uses the following encoding: utf-8
"""
技能冷却识别（纯图像逻辑：不依赖设备、不用 OCR，可以脱离游戏单测）

为什么不用模板匹配
------------------
同一个技能「冷却好」的两帧之间，图标上的高光/光环在转，实测相关系数只有 0.69；
而「冷却中」和「冷却好」的相关系数反而能到 0.73 —— 模板匹配分不开这两种状态。
冷却的本质变化是：图标上盖了一层半透明黑罩，中间再叠一个白色倒计时数字，
所以图标整体变暗、颜色变灰。据此对按钮中心一小块圆盘打分：

    分数 = 中心圆盘的「中位灰度 x 中位彩度 / 255」        （0~255 量级，越大越亮越艳）

- 用中位数：白色倒计时数字的彩度≈0、图标高光很亮，用均值会被这两者带偏
- 圆盘半径取按钮短边的 0.30：避开按钮的金属外框和按钮边角外的游戏背景
- 判定用「当前分数 / 冷却好基准分」的比例，不用绝对阈值：不同技能图标本身的明暗
  差很多（实测冷却好时中位灰度从 43 到 165），绝对值没法一套通用

基准分由「冷却好」的截图素材算出（`tasks/GameUi/res/battle/res_skill*_ready.png`），
换技能/换图标时重新裁一份素材、重跑 `AssetsExtractor('tasks/GameUi').extract()` 即可。

实测（区域1，52x54，2026-10-23 真机）
------------------------------------
- 冷却好：分数 37.7 / 39.3（两种高光样式）
- 冷却中：分数 1.4 / 0.8（倒计时 1 / 0）
所以 `SKILL_COOLDOWN_RATIO = 0.6` 有 10 倍以上的余量，不靠调阈值吃饭。
"""
import sys

import cv2
import numpy as np

# 判定用的中心圆盘半径（相对按钮短边）
SKILL_SCORE_RADIUS = 0.30
# 当前分数 < 基准分 x 该比例 -> 冷却中
SKILL_COOLDOWN_RATIO = 0.6

# 技能状态
SKILL_READY = 'ready'
SKILL_COOLING = 'cooling'
SKILL_UNKNOWN = 'unknown'

_MASK_CACHE: dict = {}


def disc_mask(height: int, width: int, radius: float = SKILL_SCORE_RADIUS) -> np.ndarray:
    """按钮中心圆盘掩码（按尺寸缓存，战斗中每帧复用）"""
    key = (int(height), int(width), round(float(radius), 3))
    mask = _MASK_CACHE.get(key)
    if mask is None:
        cy, cx = height / 2.0, width / 2.0
        r = radius * min(width, height)
        yy, xx = np.mgrid[0:height, 0:width]
        mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r
        _MASK_CACHE[key] = mask
    return mask


def skill_region_score(image, region: tuple = None, radius: float = SKILL_SCORE_RADIUS) -> float:
    """
    技能按钮区域的冷却分数（0~255 量级，越大越亮越艳）

    :param image: RGB 图（device.image / RuleImage.image 都是 RGB 顺序）
    :param region: (x, y, w, h)；不传则用整张图
    :param radius: 中心圆盘半径占短边的比例
    :return: 分数；图/区域无效返回 0（按冷却处理，保守不点）
    """
    if image is None:
        return 0.0
    if region is None:
        height, width = image.shape[:2]
        x, y, w, h = 0, 0, width, height
    else:
        x, y, w, h = [int(v) for v in region]
    height, width = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    crop = image[y0:y1, x0:x1]
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return 0.0

    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lab = cv2.cvtColor(crop, cv2.COLOR_RGB2LAB).astype(np.float32)
    chroma = np.sqrt((lab[:, :, 1] - 128.0) ** 2 + (lab[:, :, 2] - 128.0) ** 2)
    mask = disc_mask(crop.shape[0], crop.shape[1], radius)
    return float(np.median(gray[mask]) * np.median(chroma[mask]) / 255.0)


class SkillCooldownJudge:
    """
    技能冷却判定器

    :param regions: {技能位下标: (x, y, w, h)}，检测区域
    :param ready_scores: {技能位下标: 冷却好时的基准分}，缺省 = 该技能位不做判定（unknown）
    :param ratio: 当前分 < 基准分 x ratio 判定为冷却中
    """

    def __init__(self, regions: dict, ready_scores: dict = None,
                 ratio: float = SKILL_COOLDOWN_RATIO):
        self.regions = {int(k): tuple(v) for k, v in (regions or {}).items()}
        self.ready_scores = {int(k): float(v) for k, v in (ready_scores or {}).items()}
        self.ratio = float(ratio)

    @classmethod
    def from_images(cls, regions: dict, ready_images: dict,
                    ratio: float = SKILL_COOLDOWN_RATIO) -> "SkillCooldownJudge":
        """用「冷却好」的截图（RGB、尺寸与检测区域一致）建判定器"""
        ready_scores = {index: skill_region_score(image)
                        for index, image in (ready_images or {}).items()
                        if image is not None}
        return cls(regions, ready_scores, ratio)

    def has_reference(self, index: int) -> bool:
        return self.ready_scores.get(int(index), 0.0) > 0

    def reference(self, index: int) -> float:
        """冷却好基准分；没配基准返回 0"""
        return self.ready_scores.get(int(index), 0.0)

    def score(self, image, index: int) -> float:
        index = int(index)
        if index not in self.regions:
            return 0.0
        return skill_region_score(image, self.regions[index])

    def ratio_of(self, image, index: int) -> float:
        """当前分 / 基准分；没配基准返回 0"""
        reference = self.reference(index)
        if reference <= 0:
            return 0.0
        return self.score(image, index) / reference

    def state(self, image, index: int) -> str:
        """返回 SKILL_READY / SKILL_COOLING / SKILL_UNKNOWN"""
        index = int(index)
        if index not in self.regions:
            return SKILL_UNKNOWN
        if not self.has_reference(index):
            return SKILL_UNKNOWN
        score = self.score(image, index)
        return SKILL_COOLING if score < self.reference(index) * self.ratio else SKILL_READY

    def states(self, image) -> dict:
        return {index: self.state(image, index) for index in self.regions}

    def is_ready(self, image, index: int) -> bool:
        return self.state(image, index) == SKILL_READY

    def is_cooling(self, image, index: int) -> bool:
        return self.state(image, index) == SKILL_COOLING


def _main(argv: list) -> int:
    """
    手工验证：给一张真机截图和一个区域，打印分数

        toolkit\\python.exe -m tasks.GameUi.navigation.battle_skill <图片> [x,y,w,h]
    """
    if not argv:
        print(__doc__)
        print('用法: python -m tasks.GameUi.navigation.battle_skill <图片> [x,y,w,h]')
        return 1
    path = argv[0]
    region = tuple(int(v) for v in argv[1].split(',')) if len(argv) > 1 else None
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        print(f'图片读取失败: {path}')
        return 1
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    print(f'图片: {path}  区域: {region or "整图"}  分数: {skill_region_score(rgb, region):.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
