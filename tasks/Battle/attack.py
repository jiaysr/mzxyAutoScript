# This Python file uses the following encoding: utf-8
"""
战斗执行能力（BattleAttack）—— 「按预设顺序攻击」就在这里

这里放**需要设备**的部分：点技能按钮、看冷却、识别技能位、按预设排队点。
纯计算（技能目录、识别算法、排序计划、预设增删）在 tasks/Battle 的其他文件里，
那边不碰设备、可以离线单测。

用法：任务类混入（GameUi 的 SimpleBattle 已经混入了）：

    class MyTask(BattleAttack, BaseTask, GameUiAssets):
        ...

    self.battle_skill_loop(slots, check_stop=..., target_check=...)   # 唯一战斗模式（队列式）
    self.battle_queue_until(stop_check)                               # 同上的简写入口

队列模式：每轮挑「优先级最高且冷却好」的技能，连点（次数/间隔取预设里的释放节奏），
没进冷却会按 common 的 attempts / cooldown_timeout 重试 + 延后重排；每次点击后校验目标。
其余方法（battle_click_*、battle_plan、battle_skill_state ...）是配套底层能力。

混入方需要提供（BaseTask + GameUiAssets 已经都有）：
- `self.config` / `self.device`
- `self.screenshot()`（BaseTask）
- `self.BATTLE_SKILL_READY_ASSET` 里列出的素材属性（GameUiAssets 的 I_SKILL*_READY）

按钮区域来自真机实测（1280x720），单位是 (x, y, w, h)；
技能栏在右下角，普通攻击是右下角那个大按钮。
**迷窟（域外迷窟）里实测这套区域坐标逐个吻合**，所以直接复用，任务不用再录一遍素材。
"""
import random
import time
from typing import Callable, List

import numpy as np

from module.base.timer import Timer
from module.config.utils import convert_to_underscore
from module.logger import logger
from tasks.Battle import slots as battle_slots
from tasks.Battle.catalog import SkillCatalog, load_catalog
from tasks.Battle.config import DEFAULT_PRESET_ID
from tasks.Battle.cooldown import (SKILL_COOLDOWN_RATIO, SKILL_COOLING, SKILL_READY,
                                   SKILL_UNKNOWN, SkillCooldownJudge, SkillStateStabilizer,
                                   skill_region_score)
from tasks.Battle.planner import PlanEntry, build_plan, plan_summary
from tasks.Battle.presets import resolve_preset
from tasks.Battle.recognizer import SkillRecognizer, format_matches


class BattleAttack:
    """战斗执行能力（混入任务类）"""

    # 点击区域（坐标定义在 tasks/Battle/slots.py，和技能图标识别共用同一份）
    BATTLE_SKILL_REGIONS_ALL = battle_slots.BATTLE_SKILL_REGIONS
    # 当前实际存在的技能按钮：7 个位都有技能（2026-10-23 实机确认）
    BATTLE_SKILL_REGIONS = BATTLE_SKILL_REGIONS_ALL
    # 兼容旧配置：以前有空技能位时用它决定是否点空位；7 个位都是实际技能后不再生效
    BATTLE_CLICK_EMPTY_SKILLS = False
    # 普通攻击按钮区域 (x, y, w, h)
    BATTLE_ATTACK_REGION = battle_slots.BATTLE_ATTACK_REGION

    # 技能冷却 / 图标识别的判定区域 (x, y, w, h)：以各技能按钮为中心，52x54
    BATTLE_SKILL_CHECK_REGIONS = battle_slots.BATTLE_SKILL_CHECK_REGIONS
    # 各技能位「冷却好」的基准素材（GameUiAssets 常量名）；缺素材的技能位不做判定
    BATTLE_SKILL_READY_ASSET = {
        0: 'I_SKILL1_READY',
        1: 'I_SKILL2_READY',
        2: 'I_SKILL3_READY',
        3: 'I_SKILL4_READY',
        4: 'I_SKILL5_READY',
        5: 'I_SKILL6_READY',
        6: 'I_SKILL7_READY',
    }
    # 当前分 < 基准分 x 该比例 -> 冷却中
    BATTLE_SKILL_COOLDOWN_RATIO = SKILL_COOLDOWN_RATIO
    # 冷却状态防抖：新状态连续 N 次采样一致才生效
    # （冷却结束前游戏会闪一下遮罩，单帧分数 0.6~0.7，不防抖会误判成「冷却好」）
    BATTLE_SKILL_STABLE = 2

    # 队列式释放（battle_skill_loop）里每次点击的连击次数与间隔：
    # 同一点快速点两次 = 双击（技能要双击才稳定触发）
    BATTLE_SKILL_CLICKS = 2
    BATTLE_SKILL_CLICK_INTERVAL = 0.1
    # 点了没进冷却时的重试次数 / 每次等它进冷却的上限（秒）
    # 正常走「战斗预设」里的 attempts / cooldown_timeout，这里只是没拿到预设时的兜底
    BATTLE_SKILL_ATTEMPTS = 3
    BATTLE_COOLDOWN_TIMEOUT = 1.2

    # 战斗区域的点击是否写 INFO 日志。一轮技能十几行「Click (x, y) @ BATTLE_SKILLn」，
    # 会把真正的战斗日志（识别/冷却/目标）冲掉；默认只写 DEBUG，排查点击坐标时改 True。
    BATTLE_CLICK_LOG = False

    # ---------------------------------------------------------------- 单次点击
    def battle_click_once(self, region, control_name: str = 'BATTLE_BUTTON') -> None:
        """
        在按钮区域里随机取一点，点一次

        只点一次：普通攻击与技能1 冷却都在 1 秒以上，连点没有意义。
        每次点完清掉连点记录，避免被判定为「连点过多」。
        """
        x, y, w, h = region
        px = random.randint(x, x + w - 1)
        py = random.randint(y, y + h - 1)
        self.device.click(x=px, y=py, control_name=control_name, log=self.BATTLE_CLICK_LOG)
        self.device.click_record_clear()

    def battle_click_attack(self) -> None:
        """点一次普通攻击"""
        self.battle_click_once(self.BATTLE_ATTACK_REGION, 'BATTLE_ATTACK')

    def battle_click_skill(self, index: int = 0) -> None:
        """
        点一次技能

        :param index: BATTLE_SKILL_REGIONS 的下标，0 = 技能1
        """
        skills = BATTLE_SKILL_REGIONS_ALL if self.BATTLE_CLICK_EMPTY_SKILLS \
            else self.BATTLE_SKILL_REGIONS
        if not 0 <= index < len(skills):
            logger.warning(f'Skill index {index} out of range')
            return
        self.battle_click_once(skills[index], f'BATTLE_SKILL{index + 1}')

    def battle_click_skill_multi(self, index: int = 0, clicks: int = None,
                                 interval: float = None) -> None:
        """
        点技能：在按钮区域随机取一点，连点 clicks 次（默认双击）

        双击必须落在同一点、间隔很短，所以坐标只随机一次。

        :param index: BATTLE_SKILL_REGIONS 的下标，0 = 技能1
        :param clicks: 点击次数，默认 BATTLE_SKILL_CLICKS（2）
        :param interval: 两次点击之间的间隔，默认 BATTLE_SKILL_CLICK_INTERVAL（0.1s）
        """
        skills = BATTLE_SKILL_REGIONS_ALL if self.BATTLE_CLICK_EMPTY_SKILLS \
            else self.BATTLE_SKILL_REGIONS
        if not 0 <= index < len(skills):
            logger.warning(f'Skill index {index} out of range')
            return
        clicks = self.BATTLE_SKILL_CLICKS if clicks is None else max(1, int(clicks))
        interval = self.BATTLE_SKILL_CLICK_INTERVAL if interval is None else float(interval)
        x, y, w, h = skills[index]
        px = random.randint(x, x + w - 1)
        py = random.randint(y, y + h - 1)
        for i in range(clicks):
            self.device.click(x=px, y=py, control_name=f'BATTLE_SKILL{index + 1}',
                              log=self.BATTLE_CLICK_LOG)
            self.device.click_record_clear()
            if i + 1 < clicks:
                self.device.sleep(interval)

    def battle_catalog(self) -> SkillCatalog:
        """技能目录（按实例缓存）"""
        catalog = getattr(self, '_battle_catalog', None)
        if catalog is None:
            catalog = load_catalog()
            self._battle_catalog = catalog
        return catalog

    def battle_recognizer(self) -> SkillRecognizer:
        """
        技能图标识别器（按实例缓存）

        相似度阈值取配置 `battle.common.identify_threshold`，
        改完配置要重新开战（或在调试时清掉 `_battle_recognizer`）才生效。
        """
        recognizer = getattr(self, '_battle_recognizer', None)
        if recognizer is not None:
            return recognizer
        battle = self.config.battle
        threshold = float(battle.common.identify_threshold) if battle else 0.72
        recognizer = SkillRecognizer.from_catalog(self.battle_catalog(), threshold=threshold)
        self._battle_recognizer = recognizer
        logger.attr('技能识别阈值', f'{threshold:.2f}')
        return recognizer

    def battle_slot_skills(self, image=None) -> dict:
        """
        识别 7 个技能位分别带了哪个技能

        :return: {技能位下标: SlotMatch}
        """
        return self.battle_recognizer().identify(self._battle_image(image),
                                                 self.BATTLE_SKILL_CHECK_REGIONS)

    def battle_log_slot_skills(self, image=None) -> dict:
        """输出一行技能位识别结果（真机标定用，和 battle_log_skill_states 配套）"""
        matches = self.battle_slot_skills(image)
        logger.info(f'技能位识别：{format_matches(matches, self.battle_catalog())}')
        return matches

    def battle_task_preset(self) -> str:
        """
        任务自己配的「战斗配置」预设 id（没配返回空串）

        任务配置里的字段名统一叫 `battle_preset`（是 `str`，前端渲染成预设下拉框），
        放在任务自己的 `battle_config` 组里，默认值就是系统预设（default）。
        用 `self.config.task.command` 拿到当前任务名，再去它的配置组里取。
        """
        task = getattr(self.config, 'task', None)
        command = getattr(task, 'command', '') or ''
        if not command:
            return ''
        task_config = getattr(self.config.model, convert_to_underscore(command), None)
        if task_config is None:
            return ''
        return str(getattr(task_config, 'battle_preset', '') or '')

    def battle_task_battle_config(self):
        """
        当前任务「战斗配置」组的对象（没有任务上下文时返回 None）

        字段都放在各任务 config 的 `battle_config` 组里，这里按
        `self.config.task.command` 反查，供 use_normal_attack / fast_relock 这类开关使用。
        """
        task = getattr(self.config, 'task', None)
        command = getattr(task, 'command', '') or ''
        if not command:
            return None
        task_config = getattr(self.config.model, convert_to_underscore(command), None)
        if task_config is None:
            return None
        return getattr(task_config, 'battle_config', None)

    def battle_use_normal_attack(self) -> bool:
        """
        当前任务是否允许点击普通攻击（任务「战斗配置」里的 use_normal_attack，默认关闭）

        没有任务上下文（如直接跑战斗试打脚本）时按关闭处理。
        """
        return bool(getattr(self.battle_task_battle_config(), 'use_normal_attack', False))

    def battle_fast_relock(self) -> bool:
        """
        当前任务是否开启「目标死了原地补锁」（任务「战斗配置」里的 fast_relock）

        只对连续刷怪、且要求在限定时间内尽量多杀的场景有意义：
        一刀一只时每只怪都要重锁，完整重锁 ~1.1s，补锁只要 ~0.3s。
        单 boss（世界首领）、刷怪间隔长（5s+，等刷新比省那 0.8s 更划算）的场景保持关闭，
        走正常流程（目标丢失 -> 结束循环 -> 外层重新锁）。
        """
        return bool(getattr(self.battle_task_battle_config(), 'fast_relock', False))

    def battle_preset(self) -> dict:
        """
        这一场实际使用的预设：任务配置里选的那个，没选就用系统预设

        （id 写错/预设被删都会自动回落到系统预设，不会因为配置不同步就打不出技能）
        """
        battle = self.config.battle
        catalog = self.battle_catalog()
        preset_id = self.battle_task_preset() or DEFAULT_PRESET_ID
        preset = resolve_preset(battle, preset_id, catalog)
        logger.attr('战斗预设', f'{preset["name"]}（{preset["id"]}）')
        return preset

    def battle_release_config(self, preset: dict = None) -> dict:
        """
        技能释放节奏：连点次数 / 点击间隔 / 重试次数 / 冷却等待上限

        取「战斗预设」里的值 —— **每个预设各一份**（`clicks` / `click_interval` /
        `attempts` / `cooldown_timeout`），所以不同战术可以有不同的点法。
        不传 preset 时按任务配置里选的预设取；系统预设只读，用的是固定默认值。

        :param preset: 已解析好的预设 dict，不传则按任务配置现取
        :return: {'clicks', 'click_interval', 'attempts', 'cooldown_timeout'}
        """
        if preset is None:
            try:
                preset = self.battle_preset()
            except Exception as e:  # noqa: BLE001 - 老配置/缺字段时不能因为取预设就崩
                logger.warning(f'读取战斗预设失败，释放节奏用默认值：{e}')
                preset = {}
        return {
            'clicks': max(1, int(preset.get('clicks') or self.BATTLE_SKILL_CLICKS)),
            'click_interval': float(preset.get('click_interval')
                                    or self.BATTLE_SKILL_CLICK_INTERVAL),
            'attempts': max(1, int(preset.get('attempts') or self.BATTLE_SKILL_ATTEMPTS)),
            'cooldown_timeout': float(preset.get('cooldown_timeout')
                                      or self.BATTLE_COOLDOWN_TIMEOUT),
        }

    def battle_plan(self, image=None, preset: dict = None) -> List[PlanEntry]:
        """
        生成这一场的释放计划：识别技能位 -> 按预设的顺序排

        「未识别 + 冷却中」的技能位先不入队：冷却遮罩和倒计时数字会让图标识别分掉到
        阈值以下，这时认不出来不代表它不该放（也可能正好是被禁用的技能）。这种位
        记到 `_battle_pending_slots`，等冷却结束后由 `battle_recheck_pending()` 重认，
        再按所属预设决定是否入队。

        :param image: 不传就用当前截图
        :param preset: 预设（默认按 battle_preset() 自己取）
        :return: PlanEntry 列表（按释放先后）
        """
        catalog = self.battle_catalog()
        if preset is None:
            preset = self.battle_preset()
        order = catalog.normalize_order(list(preset.get('order') or []),
                                        list(preset.get('disabled') or []))
        matches = self.battle_slot_skills(image)
        common = getattr(self.config.battle, 'common', None)
        include_unknown = bool(getattr(common, 'click_unknown_slot', True))
        unknown_min_score = float(getattr(common, 'unknown_slot_min_score', 0.6))
        plan = build_plan(matches, order, catalog,
                          disabled=list(preset.get('disabled') or []),
                          include_unknown=include_unknown,
                          unknown_min_score=unknown_min_score)
        logger.info(f'技能位识别：{format_matches(matches, catalog)}')

        # 未识别且正在冷却：图标被遮罩/数字盖住，识别不可信，先不入队
        states = self.battle_states_raw(image)
        pending = {slot: preset
                   for slot, match in matches.items()
                   if match.skill_id is None and states.get(slot) == SKILL_COOLING}
        if pending:
            plan = [entry for entry in plan if entry.slot not in pending]

        planned = {entry.slot for entry in plan}
        dropped = [f'技能{slot + 1}({match.score:.2f})'
                   for slot, match in sorted(matches.items())
                   if not match.skill_id and slot not in planned and slot not in pending]
        if dropped:
            logger.info(f'跳过不像技能的技能位（带的可能是物品等）：{", ".join(dropped)}')
        if pending:
            logger.info(f'技能位{[slot + 1 for slot in sorted(pending)]}未识别且在冷却，'
                        f'先不入队（冷却结束后重认）')
        self._battle_pending_slots = pending
        self._battle_plan_entries = plan

        logger.info(f'释放计划（预设 {preset.get("name")}，{len(plan)} 个技能）：{plan_summary(plan)}')
        return plan

    def battle_has_pending_slots(self) -> bool:
        """是否有等待冷却后重认的技能位（未识别 + 识别时在冷却）"""
        return bool(getattr(self, '_battle_pending_slots', None))

    def battle_current_slots(self) -> List[int]:
        """最近一次计划（含冷却后重认补进来的技能位）的释放顺序"""
        return [entry.slot for entry in
                (getattr(self, '_battle_plan_entries', None) or [])]

    def battle_recheck_pending(self, image=None) -> List[PlanEntry] | None:
        """
        重认等待中的技能位：冷却结束后再识别一次，决定是否入队

        只重认状态不再是 `cooling` 的位（冷却遮罩下的识别结果不可信）。
        重认出来就按它所属预设的顺序/禁用处理；仍然认不出来则按
        `click_unknown_slot` / `unknown_slot_min_score` 决定。

        :param image: 传入当前帧则不重新截图
        :return: 更新后的完整计划；没有技能位结束等待时返回 None
        """
        pending = getattr(self, '_battle_pending_slots', None)
        if not pending:
            return None

        image = self._battle_image(image)
        states = self.battle_states_raw(image)
        catalog = self.battle_catalog()
        common = getattr(self.config.battle, 'common', None)
        include_unknown = bool(getattr(common, 'click_unknown_slot', True))
        unknown_min_score = float(getattr(common, 'unknown_slot_min_score', 0.6))

        entries = list(getattr(self, '_battle_plan_entries', None) or [])
        remaining = {}
        resolved = []
        for slot in sorted(pending):
            preset = pending[slot]
            if states.get(slot) == SKILL_COOLING:
                remaining[slot] = preset
                continue
            matches = self.battle_recognizer().identify(
                image, {slot: self.BATTLE_SKILL_CHECK_REGIONS[slot]})
            order = catalog.normalize_order(list(preset.get('order') or []),
                                            list(preset.get('disabled') or []))
            added = build_plan(matches, order, catalog,
                               disabled=list(preset.get('disabled') or []),
                               include_unknown=include_unknown,
                               unknown_min_score=unknown_min_score)
            resolved.append(slot)
            entries = [entry for entry in entries if entry.slot != slot] + added
            if added:
                logger.info(f'技能位{slot + 1}冷却结束重认：{plan_summary(added)}')
            else:
                logger.info(f'技能位{slot + 1}冷却结束重认后仍不入队（未识别或预设禁用）')

        self._battle_pending_slots = remaining
        if not resolved:
            return None

        recognized = sorted([entry for entry in entries if entry.recognized],
                            key=lambda entry: (entry.priority, entry.slot))
        unknown = sorted([entry for entry in entries if not entry.recognized],
                         key=lambda entry: entry.slot)
        entries = recognized + unknown
        self._battle_plan_entries = entries
        logger.info(f'重认后释放计划（{len(entries)} 个技能）：{plan_summary(entries)}')
        return entries

    def battle_plan_slots(self, image=None, preset: dict = None,
                          fallback_all: bool = False) -> List[int]:
        """
        这一场要点哪些技能位

        - 识别成功：按计划顺序（预设优先级 + 禁用过滤；像技能但没认出来的排最后）
        - **未识别且识别时在冷却的技能位先不入队**：冷却遮罩会让图标识别失真，
          冷却结束后由 `battle_recheck_pending()` 重认，再决定是否入队
        - **一个都没认出来：返回空列表，不盲点全部技能位**
          技能位上带的不一定是技能 —— 可能是物品/药品/脱离卡，盲点会白耗道具，
          在战场里甚至会直接跳出战场（战车战场第 7 位实测就是物品，识别分只有 0.50）。
          空列表时 battle_skill_loop 只会补普通攻击。

        :param image: 传入当前帧则不重新截图
        :param preset: 指定预设（不传按任务配置）
        :param fallback_all: True 时保留旧行为（识别失败就点全部技能位），仅调试用
        :return: 技能位下标列表
        """
        plan = self.battle_plan(image=image, preset=preset)
        if plan:
            return [entry.slot for entry in plan]
        if fallback_all:
            logger.warning('技能位一个都没识别出来，按全部技能位逐个尝试')
            return list(range(len(self.BATTLE_SKILL_REGIONS)))
        logger.warning('技能位一个都没识别出来，本轮不放技能'
                       '（技能位上可能带的是物品，盲点会白耗道具）')
        return []
    def battle_skill_release(self, slot: int, image=None,
                             target_check: Callable[[np.ndarray], bool] = None,
                             max_attempts: int = 3, cooldown_timeout: float = 1.2,
                             poll_interval: float = 0.1, clicks: int = None,
                             click_interval: float = None) -> str:
        """
        尝试释放一个技能：双击 -> 判断目标 -> 判断冷切；没进冷切就重试。

        识别只做两次：
        - 点击前用调用方传入的 image 判断目标（不重复截图）；
        - 点击后的「目标 + 冷切」合并到同一个轮询里（每帧一张图做两项识别）。

        :param slot: 技能位下标
        :param image: 点击前用于目标判断的截图，None = 现截
        :param target_check: 接收截图、返回 False 表示目标丢失
        :param max_attempts: 最多点几次（含第一次）
        :param cooldown_timeout: 点完等它进入冷却的最长时间（秒）
        :param poll_interval: 冷却轮询间隔（秒）
        :param clicks: 每个技能连点几次，默认 BATTLE_SKILL_CLICKS（2）
        :param click_interval: 同一技能两次点击的间隔，默认 BATTLE_SKILL_CLICK_INTERVAL（0.1）
        :return: 'cooled' 进入冷却；'failed' 多次尝试仍未进入；'target_lost' 目标丢失
        """
        for attempt in range(1, max_attempts + 1):
            if image is None:
                image = self.screenshot()
            if target_check is not None and not target_check(image):
                logger.warning('目标丢失，停止释放技能')
                return 'target_lost'
            self.battle_click_skill_multi(slot, clicks=clicks, interval=click_interval)
            image = None
            result = self.battle_wait_cooldown_checked(
                slot, target_check=target_check,
                timeout=cooldown_timeout, poll_interval=poll_interval)
            if result == 'cooled':
                return 'cooled'
            if result == 'target_lost':
                return 'target_lost'
            logger.info(f'技能{slot + 1} 第{attempt}次点击后未进入冷却')
        logger.warning(f'技能{slot + 1} 点了 {max_attempts} 次仍未进入冷却')
        return 'failed'

    def battle_queue_until(self, stop_check: Callable[[], bool],
                           target_check: Callable[[np.ndarray], bool] = None,
                           include_attack: bool = True, **kwargs) -> str:
        """
        队列模式的兼容入口：一直打，直到 `stop_check()` 返回 True

        等价于旧「一直打到 stop_check 为真」的用法，但走队列模式
        （优先级最高、冷却好的先放；没进冷却会重试 + 延后重排）。

        :param stop_check: 无参函数，返回 True 结束
        :param target_check: 接收截图、返回 False 表示目标丢失；默认不按目标丢失停
        :param include_attack: 全部技能冷却时是否点普通攻击（默认 True，和旧循环一致）
        :param kwargs: 透传给 battle_skill_loop（timeout / clicks / click_interval ...）
        :return: 'stop' / 'target_lost' / 'timeout' / 'no_slot'
        """
        slots = self.battle_plan_slots()
        if not slots:
            logger.warning('没有识别到可释放的技能位，先补普通攻击')
            if include_attack:
                self.battle_click_attack()
            return 'no_slot'
        return self.battle_skill_loop(
            slots,
            check_stop=lambda image: 'stop' if stop_check() else None,
            target_check=target_check or (lambda image: True),
            include_attack=include_attack,
            **kwargs,
        )

    def battle_skill_loop(self, slots: List[int],
                          check_stop: Callable[[], str] = None,
                          target_check: Callable[[], bool] = None,
                          timeout: float = None,
                          attack_interval: float = 1.0,
                          idle_interval: float = 0.4,
                          include_attack: bool = None,
                          max_attempts: int = None,
                          cooldown_timeout: float = None,
                          poll_interval: float = 0.1,
                          max_cycles: int = None,
                          max_skills: int = None,
                          on_cast: Callable[[int], None] = None,
                          on_target_lost: Callable[[], bool] = None,
                          recheck_pending: bool = True,
                          clicks: int = None,
                          click_interval: float = None) -> str:
        """
        队列式连续释放技能（不按轮）：

        ⚠️ 兼容保留：这是队列式实现（优先级最高且冷却好的先放，没进冷却会重试 + 延后重排），
        目前世界首领 / 仙府九重天 / 域外迷窟 / 战斗试打还在用，也是本项目的唯一战斗模式。

        - **每次都在当前顺序里挑「最高优先级、未冷却、本轮未失败过」的技能释放**，
          所以高优先级技能一冷却好就会被优先释放，不会排在别的技能后面；
        - 技能双击后没进冷却 -> 放到「下一个技能」后面再试一次（同时只允许一个延后）；
        - 下一个技能也没进冷却时不重排，本轮跳过它，继续释放之前延后的那个；
        - 所有可释放技能都试过一遍后，恢复优先级顺序开始新一轮；
        - **所有技能都在冷却时**才释放普通攻击（按 attack_interval 节流）。

        :param slots: 技能位下标列表（按优先级）
        :param check_stop: 接收截图、返回 None 继续，返回字符串（如 'reward'/'target_lost'）则停止
        :param target_check: 接收截图、返回 False 表示目标丢失
        :param timeout: 总超时（秒），None = 不限
        :param attack_interval: 普通攻击的最小间隔（秒）
        :param idle_interval: 全部技能冷却时的轮询间隔（秒）
        :param include_attack: 全部技能冷却时是否点普通攻击；None = 读任务战斗配置（默认关闭）
        :param max_attempts: 每个技能每次释放最多尝试几次（含第一次）；
                             None = 读预设的 attempts（默认 3）
        :param cooldown_timeout: 点完等它进入冷却的最长时间（秒）；
                                 None = 读预设的 cooldown_timeout（默认 1.2）
        :param poll_interval: 冷却轮询间隔（秒）
        :param max_cycles: 最多重排几轮（一轮 = 所有可释放技能都尝试过一遍），None = 不限
        :param max_skills: 最多成功释放几个技能（进入冷却算 1 个），None = 不限
        :param on_cast: 每次成功释放技能后回调，参数为本次调用内已释放的技能数；
                        用于死亡重试等中途退出的场景在外部累计释放数
        :param on_target_lost: 目标丢失时的回调（无参）。返回 True 表示「已经补锁到新目标」，
                               循环接着打（刷小怪一刀一只的场景，省掉退回外层重锁的开销）；
                               返回 False / 不传 -> 按原逻辑结束循环
        :param recheck_pending: 是否在循环里重认「未识别 + 冷却中」的技能位
                                （冷却结束后识别一次，再决定是否入队）
        :param clicks: 每个技能连点几次；None = 读预设的 clicks（默认 2）
        :param click_interval: 同一技能两次点击的间隔；None = 读战斗设置（默认 0.2）
        :return: check_stop 的返回值 / 'target_lost' / 'skill_limit' / 'timeout'
        """
        # 释放节奏取「战斗预设」（每个预设各一份），参数显式传就覆盖，方便调试
        release = self.battle_release_config()
        clicks = release['clicks'] if clicks is None else max(1, int(clicks))
        click_interval = (release['click_interval'] if click_interval is None
                          else float(click_interval))
        max_attempts = (release['attempts'] if max_attempts is None
                        else max(1, int(max_attempts)))
        cooldown_timeout = (release['cooldown_timeout'] if cooldown_timeout is None
                            else float(cooldown_timeout))
        logger.attr('释放节奏', f'连点 {clicks} 次 / 间隔 {click_interval}s / '
                                f'重试 {max_attempts} 次 / 冷却超时 {cooldown_timeout}s')

        order = list(slots)
        recheck = getattr(self, 'battle_recheck_pending', None) if recheck_pending else None
        has_pending = getattr(self, 'battle_has_pending_slots', None)
        if not order:
            if recheck is None or not (has_pending is not None and has_pending()):
                logger.warning('没有可释放的技能位')
                return 'timeout'
            logger.info('当前没有可释放的技能位，等待冷却结束后重认')

        base_order = list(order)
        # 本轮已经失败过、暂时不再尝试的技能（新一轮开始时清空）
        blocked = set()
        # 当前被延后到「下一个技能」后面的技能（同时只允许一个）
        deferred = None
        timer = Timer(timeout).start() if timeout else None
        cycles = 0
        casts = 0
        next_attack = 0.0
        if include_attack is None:
            include_attack = self.battle_use_normal_attack()

        logger.hr('队列式释放技能', level=2)
        logger.info(f'技能顺序：{[slot + 1 for slot in base_order]}')
        logger.attr('普通攻击', '开启' if include_attack else '关闭')

        while True:
            # 每轮一张截图：停止条件、目标/按钮、7 个技能位冷切都从这张图识别
            image = self.screenshot()
            if check_stop is not None:
                reason = check_stop(image)
                if reason:
                    return reason
            if timer is not None and timer.reached():
                logger.warning(f'战斗超时（{timeout}s）')
                return 'timeout'

            # 冷却结束后重认等待中的技能位，认出来就插回预设优先级里
            if recheck is not None:
                updated = recheck(image=image)
                if updated is not None:
                    order = [entry.slot for entry in updated]
                    base_order = list(order)
                    blocked.clear()
                    deferred = None
                    logger.info(f'技能位重认后更新队列：{[slot + 1 for slot in order]}')
                    if not order:
                        if has_pending is None or not has_pending():
                            logger.warning('重认后仍没有可释放的技能位')
                            return 'timeout'
                        self.device.sleep(idle_interval)
                        continue

            states = self.battle_skills_state(image=image)
            if all(states.get(slot) == SKILL_COOLING for slot in order):
                now = time.time()
                if include_attack and now >= next_attack:
                    self.battle_click_attack()
                    next_attack = now + float(attack_interval)
                self.device.sleep(idle_interval)
                continue

            # 每轮现查状态：冷却好的高优先级技能会立刻成为候选，不用等队列排到它
            slot = next((s for s in order
                         if states.get(s) != SKILL_COOLING and s not in blocked), None)
            if slot is None:
                # 没有可释放的技能：新一轮，恢复优先级顺序、清空本轮失败记录
                order = list(base_order)
                blocked.clear()
                deferred = None
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    logger.warning(f'达到最大重排次数 {max_cycles}')
                    return 'timeout'
                logger.info(f'重新按优先级排序：{[s + 1 for s in order]}')
                continue

            result = self.battle_skill_release(
                slot, image=image, target_check=target_check, max_attempts=max_attempts,
                cooldown_timeout=cooldown_timeout, poll_interval=poll_interval,
                clicks=clicks, click_interval=click_interval)
            if result == 'target_lost':
                # 目标死了/被拉走：允许调用方立刻补锁下一个目标，接着在同一轮里打
                # （刷小怪时一刀一只，每只都退回外层重锁会白花 1s 以上）
                if on_target_lost is not None and on_target_lost():
                    logger.info('已补锁到下一个目标，继续释放技能')
                    continue
                # 交给调用方再确认一次（比如等奖励面板），没有结论就按目标丢失结束
                if check_stop is not None:
                    return check_stop(self.screenshot()) or 'target_lost'
                return 'target_lost'
            if result == 'cooled':
                casts += 1
                if on_cast is not None:
                    # 计数可能因死亡抛异常中断本次调用，及时外传累计值
                    on_cast(casts)
                blocked.discard(slot)
                if deferred == slot:
                    deferred = None
                if max_skills is not None and casts >= max_skills:
                    logger.info(f'已成功释放 {casts} 个技能，达到上限 {max_skills}，结束')
                    return 'skill_limit'
                continue

            # 没进冷却
            if deferred == slot:
                # 延后的技能再次失败：本轮不再重试它
                deferred = None
                blocked.add(slot)
                logger.info(f'技能{slot + 1} 仍未进入冷却，本轮不再重试')
            elif deferred is None:
                # 第一次失败：排到「下一个技能」后面，允许再试一次
                deferred = slot
                index = order.index(slot)
                order.pop(index)
                order.insert(min(index + 1, len(order)), slot)
                logger.info(f'技能{slot + 1} 延后，排到下一个技能后面：{[s + 1 for s in order]}')
            else:
                # 已有延后技能：当前技能不重排，本轮不再尝试
                blocked.add(slot)
                logger.info(f'技能{slot + 1} 未进入冷却，已有延后技能，本轮不重排')
    def battle_wait_cooldown_checked(self, slot: int,
                                     target_check: Callable[[np.ndarray], bool] = None,
                                     timeout: float = 2.0,
                                     poll_interval: float = 0.1) -> str:
        """
        点完技能后轮询：**每帧一张截图，同时判断「目标还在吗」和「进冷却了吗」**

        一次识别做两项判断，避免点击后再单独截一张做目标校验。

        :param slot: 技能位下标
        :param target_check: 接收截图、返回 False 表示目标丢失
        :return: 'cooled' 进入冷却；'target_lost' 目标丢失；'failed' 超时仍没进冷却
        """
        timer = Timer(timeout).start()
        while True:
            image = self.screenshot()
            if target_check is not None and not target_check(image):
                logger.warning('点击技能后目标丢失')
                return 'target_lost'
            if self.battle_skill_state(slot, image=image) == SKILL_COOLING:
                return 'cooled'
            if timer.reached():
                return 'failed'
            self.device.sleep(poll_interval)
    def battle_skill_judge(self) -> SkillCooldownJudge:
        """
        冷却判定器（按任务实例缓存）：基准分从「冷却好」素材现算，
        素材是 1280x720 下对应技能位的截图，换素材后重跑一次即生效
        """
        judge = getattr(self, '_battle_skill_judge', None)
        if judge is not None:
            return judge

        ready_scores = {}
        for index, attr_name in self.BATTLE_SKILL_READY_ASSET.items():
            rule = getattr(self, attr_name, None)
            if rule is None:
                logger.warning(f'技能{index + 1}没有冷却基准素材 {attr_name}，该技能不做冷却判定')
                continue
            try:
                image = rule.image
                height, width = image.shape[:2]
                ready_scores[index] = skill_region_score(image, (0, 0, width, height))
                logger.attr(f'技能{index + 1}冷却基准分', f'{ready_scores[index]:.2f}')
            except Exception as e:  # noqa: BLE001
                logger.warning(f'技能{index + 1}冷却基准素材读取失败（{attr_name}）：{e}')

        judge = SkillCooldownJudge(self.BATTLE_SKILL_CHECK_REGIONS, ready_scores,
                                   self.BATTLE_SKILL_COOLDOWN_RATIO)
        self._battle_skill_judge = judge
        return judge

    def _battle_image(self, image=None):
        """取判定用的图：不传就用最新截图；一次都没截过就先截一张"""
        if image is not None:
            return image
        if not self.exist_image():
            return self.screenshot()
        return self.device.image

    def battle_skill_score(self, index: int, image=None) -> float:
        """技能按钮当前冷却分数（调试/校准用）"""
        return self.battle_skill_judge().score(self._battle_image(image), index)

    def battle_skill_ratio(self, index: int, image=None) -> float:
        """当前分 / 冷却好基准分（1.0 = 与冷却好时一致，调试/校准用）"""
        return self.battle_skill_judge().ratio_of(self._battle_image(image), index)

    def battle_state_stabilizer(self) -> SkillStateStabilizer:
        """冷却状态防抖器（按实例缓存，BATTLE_SKILL_STABLE = 2）"""
        stabilizer = getattr(self, '_battle_state_stabilizer', None)
        if stabilizer is None:
            stabilizer = SkillStateStabilizer(self.BATTLE_SKILL_STABLE)
            self._battle_state_stabilizer = stabilizer
        return stabilizer

    def battle_states_raw(self, image=None) -> dict:
        """未经防抖的原始冷却状态（校准/调试用）"""
        return self.battle_skill_judge().states(self._battle_image(image))

    def battle_states(self, image=None) -> dict:
        """
        所有技能位的冷却状态（**已防抖**：新状态要连续 N 次采样一致才生效）

        每调用一次算一次采样 —— 所以别拿着同一张截图反复调它（内部方法都是
        截一次图、取一次状态；要用同一个结果就自己把返回的 dict 存下来）。
        """
        return self.battle_state_stabilizer().update(self.battle_states_raw(image))

    def battle_skill_state(self, index: int, image=None) -> str:
        """技能状态（防抖后）：ready / cooling / unknown（缺基准素材时为 unknown）"""
        return self.battle_states(image).get(index, SKILL_UNKNOWN)

    def battle_skill_ready(self, index: int, image=None) -> bool:
        """技能是否冷却好"""
        return self.battle_skill_state(index, image) == SKILL_READY
    def battle_skills_state(self, image=None) -> dict:
        """所有技能位的状态（防抖后）{下标: ready/cooling/unknown}"""
        return self.battle_states(image)
    def battle_log_skill_states(self, image=None) -> dict:
        """输出一行「每个技能位的状态 + 当前分/基准分」（原始 + 防抖后），真机校准用"""
        image = self._battle_image(image)
        judge = self.battle_skill_judge()
        raw = judge.states(image)
        stable = self.battle_states(image)
        text = ' '.join(f'{index + 1}:{stable[index]}({judge.ratio_of(image, index):.2f})'
                        for index in sorted(raw))
        changed = {index + 1: f'{raw[index]}->{stable[index]}'
                   for index in sorted(raw) if raw[index] != stable[index]}
        logger.info(f'技能冷却状态 {text}'
                    + (f'  防抖中 {changed}' if changed else ''))
        return stable
