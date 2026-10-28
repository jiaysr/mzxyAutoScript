# This Python file uses the following encoding: utf-8
"""
开战前识别：7 个技能位分别带了哪个技能

为什么要在开战前识别
--------------------
技能目录里有 10+ 个技能（还会继续加），但战斗界面只有 7 个技能位，
一个角色只会带其中几个。「优先级」是技能之间的顺序，不是技能位的顺序 ——
所以先认出每个技能位上是哪个技能，再按优先级把这些技能位排好，才知道该先点哪个按钮。

只比「图标本体」
----------------
按钮外圈是金属边框 + 高光，所有技能都一样；带着它算相似度会把不相干的技能拉得很像。
所以图标素材裁的是判定区域中间 40x40 的图标本体（`tasks/Battle/slots.py: SKILL_ICON_INSET`），
识别时也从同样的位置裁（见 `identify()`），四个角用圆形 alpha 掩掉。

算法（三个分量，各管一件事）
--------------------------
1. 结构分：亮度通道 L 上的皮尔逊相关系数（掩码内）。
   对亮度整体缩放/平移不敏感 —— 高光转动、模拟器调色都不影响。
2. 色相分：Lab 里 (a, b) 平均色度向量的夹角余弦。补「同形状不同颜色」这一刀。
3. 色度直方图：(a, b) 二维直方图的相关系数。补「颜色分布」——
   结构/色相都是全局平均口径，紫色斩击和蓝色闪电这类「结构相近、配色不同」靠它分开。

    相似度 = 0.4 x 结构分 + 0.3 x 色相分 + 0.3 x 色度直方图

三个分量都是「尺度无关」的，所以真机截图和图标素材尺寸不同也没关系（内部会缩放）。

判定要过两道关（缺一不可）
--------------------------
- `score >= threshold`（默认 0.78）：绝对相似度够；
- `score - second_best >= margin`（默认 0.05）：**要跟第二名拉开**。
  图标本来就可能有长得像的，宁可判「未识别」也不要认成别的技能
  —— 认错了会按错的优先级放技能，比不认更糟。

实测（10 个真实技能图标，2026-10-25，两张 1280x720 真机截图）：
- 自身匹配 1.00，最像的一对（力斩 vs 雷鳞灭）0.74；
- 亮度 ±15% / 伽马 / 模糊扰动后自身仍有 0.87+，最优匹配不变；
所以 0.78 的阈值 + 0.05 的 margin 有富余。换图标后请用 `tests/battle_identify.py` 复测。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from module.logger import logger
from tasks.Battle.catalog import SkillCatalog, load_catalog
from tasks.Battle.slots import SKILL_ICON_INSET

# 识别相似度下限：低于该值视为「未识别」
DEFAULT_THRESHOLD = 0.78
# 与第二名的差距下限：太接近说明「分不清是哪个」，同样判未识别
DEFAULT_MARGIN = 0.05
# 三个分量的权重（见模块说明）：真实技能图标实测结构分区分度最高，色相/色度补颜色
STRUCTURE_WEIGHT = 0.4
COLOR_WEIGHT = 0.3
CHROMA_HIST_WEIGHT = 0.3
# 色度模长低于该值视为「没有颜色」（灰色图标），色相分不给惩罚
ACHROMATIC_LIMIT = 4.0
_RESIZE_INTERPOLATION = cv2.INTER_AREA


@dataclass(frozen=True)
class SlotMatch:
    """一个技能位的识别结果"""
    slot: int                  # 技能位下标（0 = 技能1）
    skill_id: Optional[str]    # 识别出的技能 id，未识别为 None
    score: float               # 相似度 0~1
    second: float = 0.0        # 第二名的分数（调试用：看「分不分的开」）

    def to_dict(self, catalog: Optional[SkillCatalog] = None) -> dict:
        skill = catalog.skill(self.skill_id) if (catalog and self.skill_id) else None
        return {
            'slot': self.slot,
            'slotName': f'技能{self.slot + 1}',
            'skill': self.skill_id,
            'name': skill.name if skill else '',
            'className': catalog.class_name(self.skill_id) if (catalog and self.skill_id) else '',
            'score': round(float(self.score), 4),
            'second': round(float(self.second), 4),
        }


# --------------------------------------------------------------------- 图像工具
def to_rgb_float(image: np.ndarray) -> np.ndarray:
    """
    统一成 float32 的 RGB 图

    **本项目的截图约定是 RGB**（device.image、RuleImage.image 都是 RGB），
    所以这里 3 通道一律当作 RGB，只做 dtype 转换、不做 BGR->RGB ——
    做了就会把红蓝对调，红技能被认成蓝技能（这个坑踩过一次）。
    cv2.imread 读进来的 BGR/BGRA 图请先在读图处转换好（见 `load_icon`）。
    """
    if image is None:
        raise ValueError('image is None')
    if image.dtype in (np.float32, np.float64):
        return image.astype(np.float32)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    elif image.shape[2] == 4:
        # 带 alpha 的图只可能来自 cv2 读图，按 BGRA 处理
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    return image.astype(np.float32)


def to_lab(rgb: np.ndarray) -> np.ndarray:
    """RGB float32 -> Lab float32（L: 0~255，a/b: 0~255，128 为中点）"""
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """皮尔逊相关系数；长度 < 3 或某一侧方差为 0 时返回 0"""
    if a.size < 3:
        return 0.0
    a = a.astype(np.float64) - float(a.mean())
    b = b.astype(np.float64) - float(b.mean())
    denom = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
    if denom < 1e-6:
        return 0.0
    return float((a * b).sum() / denom)


def load_icon(path) -> Tuple[np.ndarray, np.ndarray]:
    """
    读技能图标

    :return: (RGB float32 图, 掩码 bool 图)，掩码来自 PNG 的 alpha 通道
    """
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f'图标读取失败: {path}')
    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3]
        rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB).astype(np.float32)
        mask = alpha > 32
        if not mask.any():
            mask = np.ones(alpha.shape, dtype=bool)
    elif image.ndim == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB).astype(np.float32)
        mask = np.ones(image.shape, dtype=bool)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32)
        mask = np.ones(image.shape[:2], dtype=bool)
    return rgb, mask


def _chroma_hist(lab: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Lab 的 (a, b) 二维色度直方图（归一化），用来比「颜色分布」"""
    hist = cv2.calcHist([lab], [1, 2], mask.astype(np.uint8), [16, 16], [0, 256, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def similarity_parts(image: np.ndarray, template: np.ndarray,
                     mask: Optional[np.ndarray] = None) -> Tuple[float, float, float]:
    """
    三个分量（调试、标定时分开看）

    :return: (结构分 0~1, 色相分 0~1, 色度直方图相关 0~1)
    """
    image = to_rgb_float(image)
    template = to_rgb_float(template)
    height, width = image.shape[:2]
    if height < 8 or width < 8:
        return 0.0, 0.0, 0.0
    if template.shape[:2] != (height, width):
        template = cv2.resize(template, (width, height), interpolation=_RESIZE_INTERPOLATION)
    if mask is None:
        mask = np.ones((height, width), dtype=bool)
    elif mask.shape[:2] != (height, width):
        mask = cv2.resize(mask.astype(np.uint8), (width, height),
                          interpolation=cv2.INTER_NEAREST) > 0
    if mask.sum() < 16:
        return 0.0, 0.0, 0.0

    lab_image = to_lab(image)
    lab_template = to_lab(template)

    structure = _pearson(lab_image[:, :, 0][mask], lab_template[:, :, 0][mask])
    structure = max(0.0, min(1.0, structure))

    a_image = lab_image[:, :, 1][mask] - 128.0
    b_image = lab_image[:, :, 2][mask] - 128.0
    a_template = lab_template[:, :, 1][mask] - 128.0
    b_template = lab_template[:, :, 2][mask] - 128.0
    v_image = np.array([a_image.mean(), b_image.mean()])
    v_template = np.array([a_template.mean(), b_template.mean()])
    norm_image = float(np.linalg.norm(v_image))
    norm_template = float(np.linalg.norm(v_template))
    if norm_image < ACHROMATIC_LIMIT or norm_template < ACHROMATIC_LIMIT:
        # 有一边是灰色的：只要不是「一个有色一个没色」就给满分
        color = 1.0 if (norm_image < ACHROMATIC_LIMIT and norm_template < ACHROMATIC_LIMIT) else 0.0
    else:
        color = float(np.dot(v_image, v_template) / (norm_image * norm_template))
        color = max(0.0, min(1.0, color))

    # 色度直方图：结构/色相都是「全局平均」的口径，直方图补的是「有哪些颜色、各占多少」，
    # 对「形状相似但配色不同」的技能（比如紫色斩击 vs 蓝色闪电）区分最有用
    hist_corr = float(cv2.compareHist(_chroma_hist(lab_image, mask),
                                      _chroma_hist(lab_template, mask),
                                      cv2.HISTCMP_CORREL))
    hist_corr = max(0.0, min(1.0, hist_corr))
    return structure, color, hist_corr


def similarity(image: np.ndarray, template: np.ndarray,
               mask: Optional[np.ndarray] = None) -> float:
    """
    两张图（可以不同尺寸，内部把模板缩放到 image 的尺寸）的相似度 0~1
    """
    structure, color, hist_corr = similarity_parts(image, template, mask)
    return (STRUCTURE_WEIGHT * structure + COLOR_WEIGHT * color
            + CHROMA_HIST_WEIGHT * hist_corr)


class SkillRecognizer:
    """
    技能图标识别器

    :param templates: {技能 id: (图标图, 掩码)}
    :param threshold: 相似度下限
    :param margin: 与第二名的差距下限
    """

    def __init__(self, templates: Dict[str, Tuple[np.ndarray, np.ndarray]],
                 threshold: float = DEFAULT_THRESHOLD,
                 margin: float = DEFAULT_MARGIN) -> None:
        self.templates = templates
        self.threshold = float(threshold)
        self.margin = float(margin)

    @classmethod
    def from_catalog(cls, catalog: SkillCatalog = None,
                     threshold: float = DEFAULT_THRESHOLD,
                     margin: float = DEFAULT_MARGIN) -> 'SkillRecognizer':
        """按技能目录加载全部图标模板（缺图标的技能自动跳过）"""
        catalog = catalog or load_catalog()
        templates: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        for skill in catalog.skills:
            path = catalog.icon_path(skill.id)
            if path is None:
                logger.warning(f'技能 {skill.id}（{skill.name}）没有图标素材，不参与识别')
                continue
            try:
                templates[skill.id] = load_icon(path)
            except Exception as e:  # noqa: BLE001
                logger.warning(f'技能 {skill.id} 图标读取失败：{e}')
        return cls(templates, threshold, margin)

    # ------------------------------------------------------------------ 识别
    def scores(self, crop) -> Dict[str, float]:
        """一张技能位截图与所有技能图标的相似度 {技能 id: 分数}"""
        return {skill_id: similarity(crop, template, mask)
                for skill_id, (template, mask) in self.templates.items()}

    def match(self, crop) -> Tuple[Optional[str], float, float]:
        """
        匹配单个技能位

        :return: (技能 id 或 None, 分数, 第二名分数)
        """
        scores = self.scores(crop)
        if not scores:
            return None, 0.0, 0.0
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        skill_id, score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if score < self.threshold or (score - second) < self.margin:
            return None, score, second
        return skill_id, score, second

    def identify(self, image, regions: Dict[int, tuple]) -> Dict[int, SlotMatch]:
        """
        识别所有技能位

        每个技能位只取「图标本体」那块（`tasks/Battle/slots.py: SKILL_ICON_INSET`），
        不取整个按钮 —— 按钮外圈对所有技能都一样，带着它算相似度会把不相干的技能拉近。

        :param image: RGB 整屏截图（device.image）
        :param regions: {技能位下标: (x, y, w, h)}，传判定区域（BATTLE_SKILL_CHECK_REGIONS）
        :return: {技能位下标: SlotMatch}
        """
        image = to_rgb_float(image)
        height, width = image.shape[:2]
        inset_x, inset_y, inset_w, inset_h = SKILL_ICON_INSET
        result: Dict[int, SlotMatch] = {}
        for slot, region in sorted(regions.items()):
            x, y, w, h = [int(v) for v in region]
            # 裁图标本体：判定区域 + 内部偏移；区域比图标小的时候退回整块
            if w >= inset_x + inset_w and h >= inset_y + inset_h:
                x0, y0 = x + inset_x, y + inset_y
                x1, y1 = x0 + inset_w, y0 + inset_h
            else:
                x0, y0, x1, y1 = x, y, x + w, y + h
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(width, x1), min(height, y1)
            crop = image[y0:y1, x0:x1]
            if crop.shape[0] < 8 or crop.shape[1] < 8:
                result[slot] = SlotMatch(slot, None, 0.0)
                continue
            skill_id, score, second = self.match(crop)
            result[slot] = SlotMatch(slot, skill_id, score, second)
        return result


def format_matches(matches: Dict[int, SlotMatch],
                   catalog: Optional[SkillCatalog] = None) -> str:
    """一行日志：技能1=技能A1(0.99) 技能2=未识别(0.61/次0.58) ..."""
    catalog = catalog or load_catalog()
    parts: List[str] = []
    for slot in sorted(matches):
        match = matches[slot]
        if match.skill_id:
            skill = catalog.skill(match.skill_id)
            name = skill.name if skill else match.skill_id
            parts.append(f'技能{slot + 1}={name}({match.score:.2f})')
        else:
            parts.append(f'技能{slot + 1}=未识别({match.score:.2f}/次{match.second:.2f})')
    return ' '.join(parts)
