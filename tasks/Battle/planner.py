# This Python file uses the following encoding: utf-8
"""
释放计划：识别结果 + 组顺序 + 禁用 -> 实际点击顺序（纯逻辑，可以脱离设备单测）

例子（组顺序 A2 > A1 > A3，A3 被禁用，技能位识别结果 1:A3 2:A1 3:A2）：
    计划 = [技能位3(A2), 技能位2(A1)]，A3 因为禁用不点，技能位1 直接跳过。
没识别出技能的技能位排在最后（可配置是否点）：认不出来不等于不能用，
默认照点，免得整场一个技能都不放。
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from tasks.Battle.catalog import SkillCatalog
from tasks.Battle.recognizer import SlotMatch


@dataclass(frozen=True)
class PlanEntry:
    """计划里的一步：点哪个技能位、这个位上是什么技能、为什么排在这里"""
    slot: int                      # 技能位下标（0 = 技能1）
    skill_id: Optional[str] = None  # 识别出的技能 id；None = 未识别
    name: str = ''                  # 技能名（日志/UI 用）
    class_name: str = ''            # 职业名
    score: float = 0.0              # 识别相似度
    priority: int = 0               # 在计划里的序号（1 开始）；0 = 未识别、排在最后
    recognized: bool = True

    def to_dict(self) -> dict:
        return {
            'slot': self.slot,
            'slotName': f'技能{self.slot + 1}',
            'skill': self.skill_id,
            'name': self.name,
            'className': self.class_name,
            'score': round(float(self.score), 4),
            'priority': self.priority,
            'recognized': self.recognized,
        }

    def __str__(self) -> str:
        who = f'{self.name}({self.skill_id})' if self.skill_id else '未识别'
        return f'技能{self.slot + 1}={who}'


def build_plan(slot_matches: Dict[int, SlotMatch],
               order: Sequence[str],
               catalog: SkillCatalog,
               disabled: Iterable[str] = (),
               include_unknown: bool = True) -> List[PlanEntry]:
    """
    生成释放计划

    :param slot_matches: {技能位下标: SlotMatch}（recognizer.identify 的结果）
    :param order: 已归一化的释放顺序（技能 id 列表，高优先级在前）
    :param catalog: 技能目录
    :param disabled: 禁用的技能 id（即使混进了 order 也不会释放）
    :param include_unknown: 未识别的技能位是否也排进计划（排最后）
    :return: 点击顺序
    """
    disabled_set = {i for i in (disabled or []) if i}
    rank = {skill_id: index for index, skill_id in enumerate(order)
            if skill_id and skill_id not in disabled_set}

    recognized: List[PlanEntry] = []
    unknown: List[PlanEntry] = []
    for slot, match in sorted(slot_matches.items()):
        skill = catalog.skill(match.skill_id) if match.skill_id else None
        if skill is not None and skill.id in rank:
            recognized.append(PlanEntry(
                slot=slot, skill_id=skill.id, name=skill.name,
                class_name=catalog.class_name(skill.id),
                score=match.score, priority=rank[skill.id] + 1))
        elif skill is not None:
            # 认出来了但这组里禁用了 / 不在顺序里：不点
            continue
        elif include_unknown:
            unknown.append(PlanEntry(slot=slot, skill_id=None, name='',
                                     score=match.score, priority=0,
                                     recognized=False))

    recognized.sort(key=lambda e: (e.priority, e.slot))
    unknown.sort(key=lambda e: e.slot)
    return recognized + unknown


def plan_slots(plan: Sequence[PlanEntry]) -> List[int]:
    """计划里要点技能位的下标列表"""
    return [entry.slot for entry in plan]


def plan_summary(plan: Sequence[PlanEntry]) -> str:
    """一行日志：技能2(A2) -> 技能1(A1) -> 技能5(未识别)"""
    if not plan:
        return '空'
    return ' -> '.join(str(entry) for entry in plan)
