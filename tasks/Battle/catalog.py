# This Python file uses the following encoding: utf-8
"""
技能目录

数据在 `skills.json`（目前录入了 3 个职业共 10 个技能，后续继续补），
这里只负责加载、校验和「顺序归一化」。
不要在这个文件里写死技能名 —— 换真实技能数据只改 json 和图标，代码不用动。

顺序归一化的意义：
用户配置里存的顺序可能缺项（新增技能）、含未知 id（技能被删）、有重复（手工改过 json），
`SkillCatalog.normalize_order()` 统一成「合法、去重、补齐到全量」的顺序，
这样 UI 和战斗执行都不用自己处理脏数据。
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from module.logger import logger

BATTLE_DIR = Path(__file__).resolve().parent
RES_DIR = BATTLE_DIR / 'res'
SKILL_ICON_DIR = RES_DIR / 'skills'
CATALOG_FILE = BATTLE_DIR / 'skills.json'


@dataclass(frozen=True)
class SkillClass:
    """职业"""
    id: str
    name: str
    description: str = ''
    color: str = '#888888'

    def to_dict(self) -> dict:
        return {'id': self.id, 'name': self.name,
                'description': self.description, 'color': self.color}


@dataclass(frozen=True)
class Skill:
    """技能"""
    id: str
    class_id: str
    name: str
    description: str = ''
    icon: str = ''
    cooldown: float = 0.0
    default_priority: int = 99

    def to_dict(self, class_name: str = '') -> dict:
        return {'id': self.id, 'class': self.class_id, 'className': class_name,
                'name': self.name, 'description': self.description,
                'icon': self.icon, 'cooldown': self.cooldown,
                'default_priority': self.default_priority}


class SkillCatalog:
    """
    技能目录

    :param classes: 职业列表
    :param skills: 技能列表
    """

    def __init__(self, classes: List[SkillClass], skills: List[Skill],
                 version: int = 1) -> None:
        if not skills:
            raise ValueError('技能目录为空：tasks/Battle/skills.json 里没有 skills')
        ids = [s.id for s in skills]
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            raise ValueError(f'技能 id 重复：{duplicated}')
        class_ids = {c.id for c in classes}
        missing = sorted({s.class_id for s in skills if s.class_id not in class_ids})
        if missing:
            raise ValueError(f'技能引用了不存在的职业：{missing}')

        self.version = int(version)
        self.classes: Tuple[SkillClass, ...] = tuple(classes)
        self.skills: Tuple[Skill, ...] = tuple(skills)
        self._by_id: Dict[str, Skill] = {s.id: s for s in skills}
        self._class_by_id: Dict[str, SkillClass] = {c.id: c for c in classes}

    # ------------------------------------------------------------------ 加载
    @classmethod
    def load(cls, path=None) -> 'SkillCatalog':
        path = Path(path) if path else CATALOG_FILE
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        classes = [SkillClass(id=str(c['id']), name=str(c['name']),
                              description=str(c.get('description', '')),
                              color=str(c.get('color', '#888888')))
                   for c in data.get('classes', [])]
        skills = []
        for index, item in enumerate(data.get('skills', [])):
            skills.append(Skill(
                id=str(item['id']),
                class_id=str(item['class']),
                name=str(item.get('name', item['id'])),
                description=str(item.get('description', '')),
                icon=str(item.get('icon', '')),
                cooldown=float(item.get('cooldown', 0) or 0),
                default_priority=int(item.get('default_priority', index + 1)),
            ))
        catalog = cls(classes, skills, version=data.get('version', 1))
        logger.info(f'技能目录：{len(catalog.classes)} 个职业 / {len(catalog.skills)} 个技能')
        return catalog

    # ------------------------------------------------------------------ 查询
    def skill(self, skill_id: str) -> Optional[Skill]:
        return self._by_id.get(skill_id)

    def has(self, skill_id: str) -> bool:
        return skill_id in self._by_id

    def class_of(self, skill_id: str) -> Optional[SkillClass]:
        skill = self._by_id.get(skill_id)
        return self._class_by_id.get(skill.class_id) if skill else None

    def class_name(self, skill_id: str) -> str:
        cls = self.class_of(skill_id)
        return cls.name if cls else ''

    def by_class(self, class_id: str) -> List[Skill]:
        return [s for s in self.skills if s.class_id == class_id]

    def icon_path(self, skill_id: str) -> Optional[Path]:
        skill = self._by_id.get(skill_id)
        if skill is None or not skill.icon:
            return None
        path = SKILL_ICON_DIR / skill.icon
        return path if path.exists() else None

    # ------------------------------------------------------------------ 顺序
    def default_order(self) -> List[str]:
        """默认释放顺序：default_priority 小的在前"""
        return [s.id for s in sorted(self.skills,
                                     key=lambda s: (s.default_priority, s.id))]

    def normalize_order(self, order: Optional[Iterable[str]] = None,
                        disabled: Optional[Iterable[str]] = None) -> List[str]:
        """
        把配置里的顺序归一化成「合法、去重、补齐到全量」的顺序

        规则：
        1. 只保留目录里存在的 id，重复的只保留第一次出现的位置；
        2. 配置里没提到的技能按 default_priority 补在最后（新增技能不会丢）；
        3. disabled 里的技能整体剔除 —— 归一化后的顺序 = 实际可释放顺序。

        :param order: 用户配置的顺序（可以为空）
        :param disabled: 该组禁用的技能 id
        :return: 技能 id 列表
        """
        disabled_set = {i for i in (disabled or []) if i}
        result: List[str] = []
        seen = set()
        for skill_id in (order or []):
            if not skill_id or skill_id in seen or skill_id in disabled_set:
                continue
            if skill_id not in self._by_id:
                continue
            seen.add(skill_id)
            result.append(skill_id)
        for skill_id in self.default_order():
            if skill_id in seen or skill_id in disabled_set:
                continue
            seen.add(skill_id)
            result.append(skill_id)
        return result

    def normalize_disabled(self, disabled: Optional[Iterable[str]] = None) -> List[str]:
        """去掉禁用列表里的未知 id 和重复项，保持原顺序"""
        result: List[str] = []
        for skill_id in (disabled or []):
            if skill_id and skill_id in self._by_id and skill_id not in result:
                result.append(skill_id)
        return result

    def complete_order(self, order: Optional[Iterable[str]] = None) -> List[str]:
        """
        把顺序补齐成全部技能，**不含禁用过滤**

        配置里存这种「完整顺序 + 另一份禁用表」的形式：
        用户临时禁用一个技能再启用回来，位置不会丢（顺序里一直保留着它）。
        实际执行时要的是 `normalize_order(order, disabled)`。
        """
        result: List[str] = []
        seen = set()
        for skill_id in (order or []):
            if not skill_id or skill_id in seen or skill_id not in self._by_id:
                continue
            seen.add(skill_id)
            result.append(skill_id)
        for skill_id in self.default_order():
            if skill_id not in seen:
                seen.add(skill_id)
                result.append(skill_id)
        return result

    # ------------------------------------------------------------------ 导出
    def to_dict(self) -> dict:
        return {
            'version': self.version,
            'classes': [c.to_dict() for c in self.classes],
            'skills': [s.to_dict(class_name=self.class_name(s.id)) for s in self.skills],
            'default_order': self.default_order(),
        }


@lru_cache(maxsize=1)
def load_catalog() -> SkillCatalog:
    """加载技能目录（进程内缓存；改 skills.json 后重启服务或调用 reload_catalog）"""
    return SkillCatalog.load()


def reload_catalog() -> SkillCatalog:
    """清缓存后重新加载（改完 json 不想重启进程时用）"""
    load_catalog.cache_clear()
    return load_catalog()
