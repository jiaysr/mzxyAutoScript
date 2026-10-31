# This Python file uses the following encoding: utf-8
"""
战斗执行能力（BattleAttack）—— 「按预设顺序攻击」就在这里

这里放**需要设备**的部分：点技能按钮、看冷却、识别技能位、按预设排队点。
纯计算（技能目录、识别算法、排序计划、预设增删）在 tasks/Battle 的其他文件里，
那边不碰设备、可以离线单测。

用法：任务类混入（GameUi 的 SimpleBattle 已经混入了，任务直接调 `self.battle_attack()`）：

    class MyTask(BattleAttack, BaseTask, GameUiAssets):
        ...

    self.battle_attack()                     # 按任务配置的预设打 1 轮
    self.battle_attack(times=3)              # 连打 3 轮
    self.battle_attack(preset='preset_1')    # 临时指定预设
    self.battle_skill_loop(slots, check_stop=..., target_check=...)  # 队列式双击+校验（目标/冷切）

对外只暴露 `battle_attack()` 这一个「攻击」入口；其余方法（battle_click_*、battle_plan、
battle_skill_state ...）是配套的底层能力，调试/监控脚本会用。

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

    # 每个按钮连点次数与间隔（秒）
    BATTLE_CLICK_TIMES = 3
    BATTLE_CLICK_INTERVAL = 0.2
    # 不同按钮之间的间隔（秒）
    BATTLE_SKILL_INTERVAL = 0.3

    # 队列式释放（battle_skill_loop）里每次点击的连击次数与间隔：
    # 同一点快速点两次 = 双击（技能要双击才稳定触发）
    BATTLE_SKILL_CLICKS = 2
    BATTLE_SKILL_CLICK_INTERVAL = 0.1

    # ---------------------------------------------------------------- 单次点击
    def battle_click_once(self, region, control_name: str = 'BATTLE_BUTTON') -> None:
        """
        在按钮区域里随机取一点，点一次

        与 battle_click_button 的区别：这里只点一次，
        用于「按冷却节奏逐次攻击」的场景（普通攻击与技能1 冷却都在 1 秒以上，
        连点没有意义）。每次点完清掉连点记录，避免被判定为「连点过多」。
        """
        x, y, w, h = region
        px = random.randint(x, x + w - 1)
        py = random.randint(y, y + h - 1)
        self.device.click(x=px, y=py, control_name=control_name)
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
            self.device.click(x=px, y=py, control_name=f'BATTLE_SKILL{index + 1}')
            self.device.click_record_clear()
            if i + 1 < clicks:
                self.device.sleep(interval)

    def battle_buttons(self) -> list:
        """本次要按的全部按钮区域（技能 + 普通攻击）；是否带空技能位由 BATTLE_CLICK_EMPTY_SKILLS 决定"""
        skills = self.BATTLE_SKILL_REGIONS_ALL if self.BATTLE_CLICK_EMPTY_SKILLS else self.BATTLE_SKILL_REGIONS
        return list(skills) + [self.BATTLE_ATTACK_REGION]

    def battle_click_button(self, region) -> None:
        """
        点一个按钮：区域内随机取点，连点 BATTLE_CLICK_TIMES 次，每次间隔 BATTLE_CLICK_INTERVAL
        """
        x, y, w, h = region
        for _ in range(self.BATTLE_CLICK_TIMES):
            px = random.randint(x, x + w - 1)
            py = random.randint(y, y + h - 1)
            self.device.click(x=px, y=py, control_name='BATTLE_SKILL')
            self.device.click_record_clear()
            self.device.sleep(self.BATTLE_CLICK_INTERVAL)

    def battle_one_group(self) -> None:
        """
        打一组：现有技能 + 普通攻击各用一次，顺序随机；
        每个按钮连点 3 次，按钮之间停 300ms

        「按优先级释放」请用 `battle_play_plan()`；这里保留给没开识别 / 旧的连点场景。
        """
        buttons = self.battle_buttons()
        random.shuffle(buttons)
        logger.info(f'释放一组技能（随机顺序）：{[b for b in buttons]}')
        for region in buttons:
            self.battle_click_button(region)
            self.device.sleep(self.BATTLE_SKILL_INTERVAL)

    # ---------------------------------------------------------------- 技能识别 / 释放计划
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

    def battle_use_normal_attack(self) -> bool:
        """
        当前任务是否允许点击普通攻击（任务「战斗配置」里的 use_normal_attack，默认关闭）

        没有任务上下文（如直接跑战斗试打脚本）时按关闭处理。
        """
        task = getattr(self.config, 'task', None)
        command = getattr(task, 'command', '') or ''
        if not command:
            return False
        task_config = getattr(self.config.model, convert_to_underscore(command), None)
        if task_config is None:
            return False
        battle_config = getattr(task_config, 'battle_config', None)
        return bool(getattr(battle_config, 'use_normal_attack', False))

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

    def battle_plan(self, image=None, preset: dict = None) -> List[PlanEntry]:
        """
        生成这一场的释放计划：识别技能位 -> 按预设的顺序排

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
        plan = build_plan(matches, order, catalog,
                          disabled=list(preset.get('disabled') or []))
        logger.info(f'技能位识别：{format_matches(matches, catalog)}')
        logger.info(f'释放计划（预设 {preset.get("name")}，{len(plan)} 个技能）：{plan_summary(plan)}')
        return plan

    def battle_play_plan(self, plan: List[PlanEntry],
                         respect_cooldown: bool = None,
                         interval: float = None,
                         include_attack: bool = True,
                         log: bool = True) -> int:
        """
        按计划放一轮技能

        每个技能点一次（不是连点）：技能冷却都在 1 秒以上，连点只会重复点同一个位置。
        开了 respect_cooldown 时先截图判断冷却，冷却中的跳过（unknown 照点，
        避免某技能位没有基准素材就整场不放）。
        技能放完补一次普通攻击（普攻没有冷却位判定，稳定补伤害；和旧的随机连点行为一致）。

        :param plan: battle_plan() 的结果
        :param respect_cooldown: 默认取配置 battle.common.respect_cooldown
        :param interval: 技能之间间隔，默认取配置 battle.common.cast_interval
        :param include_attack: 放完技能后是否补一次普通攻击
        :param log: 是否输出每一轮的日志（循环调用时传 False，自己汇总日志）
        :return: 实际点了几个技能（不含普通攻击）
        """
        battle = self.config.battle
        if respect_cooldown is None:
            respect_cooldown = bool(battle.common.respect_cooldown) if battle else True
        if interval is None:
            interval = float(battle.common.cast_interval) if battle else 0.2

        states = {}
        if respect_cooldown:
            self.screenshot()
            states = self.battle_states()  # 一轮只采样一次，别在循环里重复查同一个技能
        clicked = 0
        for entry in plan:
            slot = entry.slot
            if respect_cooldown:
                state = states.get(slot, SKILL_UNKNOWN)
                if state == SKILL_COOLING:
                    if log:
                        logger.info(f'{entry} 冷却中，跳过')
                    continue
            self.battle_click_skill(slot)
            clicked += 1
            self.device.sleep(interval)
        if include_attack:
            self.battle_click_attack()
            self.device.sleep(interval)
        if log:
            logger.info(f'本轮释放 {clicked}/{len(plan)} 个技能'
                        f'{" + 普通攻击" if include_attack else ""}')
        return clicked

    def battle_skill_release(self, slot: int, image=None,
                             target_check: Callable[[np.ndarray], bool] = None,
                             max_attempts: int = 3, cooldown_timeout: float = 1.2,
                             poll_interval: float = 0.1) -> str:
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
        :return: 'cooled' 进入冷却；'failed' 多次尝试仍未进入；'target_lost' 目标丢失
        """
        for attempt in range(1, max_attempts + 1):
            if image is None:
                image = self.screenshot()
            if target_check is not None and not target_check(image):
                logger.warning('目标丢失，停止释放技能')
                return 'target_lost'
            self.battle_click_skill_multi(slot)
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

    def battle_skill_loop(self, slots: List[int],
                          check_stop: Callable[[], str] = None,
                          target_check: Callable[[], bool] = None,
                          timeout: float = None,
                          attack_interval: float = 1.0,
                          idle_interval: float = 0.4,
                          include_attack: bool = None,
                          max_attempts: int = 3,
                          cooldown_timeout: float = 1.2,
                          poll_interval: float = 0.1,
                          max_cycles: int = None) -> str:
        """
        队列式连续释放技能（不按轮）：

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
        :param max_attempts: 每个技能每次释放最多尝试几次（含第一次）
        :param cooldown_timeout: 点完等它进入冷却的最长时间（秒）
        :param poll_interval: 冷却轮询间隔（秒）
        :param max_cycles: 最多重排几轮（一轮 = 所有可释放技能都尝试过一遍），None = 不限
        :return: check_stop 的返回值 / 'target_lost' / 'timeout'
        """
        order = list(slots)
        if not order:
            logger.warning('没有可释放的技能位')
            return 'timeout'

        base_order = list(order)
        # 本轮已经失败过、暂时不再尝试的技能（新一轮开始时清空）
        blocked = set()
        # 当前被延后到「下一个技能」后面的技能（同时只允许一个）
        deferred = None
        timer = Timer(timeout).start() if timeout else None
        cycles = 0
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
                cooldown_timeout=cooldown_timeout, poll_interval=poll_interval)
            if result == 'target_lost':
                # 交给调用方再确认一次（比如等奖励面板），没有结论就按目标丢失结束
                if check_stop is not None:
                    return check_stop(self.screenshot()) or 'target_lost'
                return 'target_lost'
            if result == 'cooled':
                blocked.discard(slot)
                if deferred == slot:
                    deferred = None
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

    def battle_attack(self, times: int = 1, preset: str = '',
                      plan: List[PlanEntry] = None, interval: float = None,
                      respect_cooldown: bool = None,
                      include_attack: bool = True) -> int:
        """
        按战斗预设的顺序攻击（对外暴露的简单入口）

        一次调用 = 识别技能栏一次 + 按预设优先级打 [times] 轮。
        每轮里每个技能点一次（可选只点冷却好的），最后补一次普通攻击。
        技能栏在一场战斗里不会变，所以只识别一次，多轮复用这份计划。

        用法（任务里）：
            self.battle_attack()                     # 按任务配置的预设打 1 轮
            self.battle_attack(times=3)              # 连打 3 轮
            self.battle_attack(preset='preset_1')    # 临时指定预设（调试用）

        :param times: 打几轮（至少 1 轮）
        :param preset: 预设 id；空 = 按任务配置（没配就是系统预设）
        :param plan: 直接传现成的计划（复用识别结果/调试），不传就现截现识别
        :param interval: 技能之间间隔（默认取配置 battle.common.cast_interval）
        :param respect_cooldown: 是否只点冷却好的（默认取配置）
        :param include_attack: 每轮最后是否补普通攻击
        :return: 总共点了多少个技能（不含普通攻击）
        """
        times = max(1, int(times or 1))
        battle = self.config.battle
        if interval is None:
            interval = float(battle.common.cast_interval) if battle else 0.2

        if plan is None:
            self.screenshot()
            if preset:
                # 指定预设优先（调试/测试用），否则按任务配置 -> 系统预设
                preset_data = resolve_preset(battle, preset, self.battle_catalog())
                plan = self.battle_plan(preset=preset_data)
            else:
                plan = self.battle_plan()

        if not plan:
            logger.warning('技能位一个都没认出来，退回随机顺序连点')
            for _ in range(times):
                self.battle_one_group()
            return 0

        total = 0
        for round_index in range(times):
            total += self.battle_play_plan(plan, respect_cooldown=respect_cooldown,
                                           interval=interval,
                                           include_attack=include_attack)
            if round_index + 1 < times:
                self.device.sleep(interval)
        logger.info(f'按预设攻击完成：{times} 轮，共释放 {total} 个技能')
        return total

    def battle_wait_cooldown(self, slot: int, timeout: float = 2.0,
                             poll_interval: float = 0.1) -> bool:
        """
        点完技能后等它进入冷却（连点完马上判可能还没变灰，所以要轮询）

        :return: 确认进入冷却 True；超时 False
        """
        timer = Timer(timeout).start()
        while True:
            self.screenshot()
            if self.battle_skill_state(slot) == SKILL_COOLING:
                return True
            if timer.reached():
                return False
            self.device.sleep(poll_interval)

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

    def battle_attack_loop(self, preset: str = '', clicks_per_skill: int = None,
                           click_interval: float = None, cooldown_timeout: float = 2.0,
                           poll_interval: float = 0.1, unknown_wait: float = 1.0,
                           include_attack: bool = True, attack_interval: float = 1.0,
                           stop_check: Callable[[], bool] = None,
                           reidentify_casts: int = 0) -> None:
        """
        按预设优先级**一直**攻击（优先级调度，不是一轮把所有技能点一遍）

        每一轮：
        1. 现截一张，判断 7 个技能位的冷却状态；
        2. 从优先级最高的技能往下找，挑「冷却好（或判不出来）」且没被临时跳过的那个；
        3. 点它 [clicks_per_skill] 次（默认 3 次），然后轮询确认它**真的进了冷却**；
        4. 回到第 1 步 —— 所以高优先级的技能一冷却好就会立刻「插队」先放。

        没有技能可放时只补普通攻击（按 attack_interval 节流），绝不硬点冷却中的技能。
        技能位一个都没识别出来时先不点，1 秒后重试识别。

        停止条件还没定：不传 stop_check 就是无限循环（Ctrl+C 结束）。

        :param preset: 预设 id；空 = 按任务配置（没配就是系统预设）
        :param clicks_per_skill: 每个技能点几次（默认 BATTLE_CLICK_TIMES = 3）
        :param click_interval: 技能内连点间隔（默认 BATTLE_CLICK_INTERVAL = 0.2）
        :param cooldown_timeout: 点完等它进入冷却的最长等待（秒）
        :param poll_interval: 等冷却 / 无技能可放时的轮询间隔（秒）
        :param unknown_wait: 冷却状态判不出来（该技能没有基准素材）时，先晾这么久再允许点
        :param include_attack: 没有技能可放时补普通攻击
        :param attack_interval: 普通攻击的最小间隔（秒）
        :param stop_check: 无参函数，返回 True 就结束；None = 一直打
        :param reidentify_casts: 每放 N 次技能重新识别一次技能栏（换装备后能跟上）；0 = 不重识别
        """
        battle = self.config.battle
        clicks_per_skill = max(1, int(clicks_per_skill or self.BATTLE_CLICK_TIMES))
        if click_interval is None:
            click_interval = float(self.BATTLE_CLICK_INTERVAL)
        preset_data = resolve_preset(battle, preset, self.battle_catalog()) if preset else None

        def _make_plan() -> List[PlanEntry]:
            self.screenshot()
            return self.battle_plan(preset=preset_data)

        plan = _make_plan()
        logger.hr('按预设优先级循环攻击', level=2)
        if plan:
            logger.info(f'开始循环：{len(plan)} 个技能，优先级 {plan_summary(plan)}')
        else:
            logger.warning('技能位一个都没识别出来，先不点击，1 秒后重试识别')

        casts = 0
        # {slot: 时间戳}：暂时跳过的技能位（判不出冷却 / 点了没进冷却）
        blocked_until: dict = {}
        next_attack = 0.0

        while True:
            if stop_check is not None and stop_check():
                logger.info(f'停止条件命中，结束循环（共释放 {casts} 次技能）')
                return

            if not plan:
                self.device.sleep(1.0)
                plan = _make_plan()
                continue

            # 每轮现截一张：优先级高的技能可能已经冷却好了，要能插队
            self.screenshot()
            states = self.battle_skills_state()
            now = time.time()

            target = None
            for entry in plan:
                state = states.get(entry.slot, SKILL_UNKNOWN)
                if state == SKILL_COOLING:
                    continue
                if blocked_until.get(entry.slot, 0.0) > now:
                    continue
                target = entry
                break

            if target is None:
                # 没有技能可放：补一次普通攻击（节流），然后隔一会儿再看
                if include_attack and now >= next_attack:
                    self.battle_click_attack()
                    next_attack = now + float(attack_interval)
                self.device.sleep(poll_interval)
                continue

            state = states.get(target.slot, SKILL_UNKNOWN)
            logger.info(f'释放 {target}（{clicks_per_skill} 连击）')
            for index in range(clicks_per_skill):
                self.battle_click_skill(target.slot)
                if index + 1 < clicks_per_skill:
                    self.device.sleep(click_interval)

            casts += 1
            if state == SKILL_UNKNOWN:
                # 没有基准素材判不出冷却：先晾一会儿，避免反复点同一个
                blocked_until[target.slot] = time.time() + float(unknown_wait)
                logger.info(f'{target} 冷却状态未知（没基准素材），{unknown_wait:.1f}s 内不再点')
            elif not self.battle_wait_cooldown(target.slot, timeout=cooldown_timeout,
                                               poll_interval=poll_interval):
                blocked_until[target.slot] = time.time() + float(unknown_wait)
                logger.warning(f'{target} 连点 {clicks_per_skill} 次后还没进入冷却，'
                               f'先跳过 {unknown_wait:.1f}s')

            if reidentify_casts and casts % int(reidentify_casts) == 0:
                plan = _make_plan()

    def battle_identify(self) -> List[PlanEntry]:
        """
        开战前的准备动作：识别技能栏并按当前预设算好计划

        识别失败时返回空列表，调用方退回 battle_one_group() 的随机连点。
        「开战前识别技能栏」是固定行为，没有开关（20 个技能不可能都在身上，
        不识别就不知道哪个按钮是哪个技能）。
        """
        battle = self.config.battle
        if battle is None:
            logger.warning('没有 battle 配置（老版本 config.json），改用随机顺序连点')
            return []
        self.screenshot()
        plan = self.battle_plan()
        if not plan:
            logger.warning('技能位一个都没认出来，改用随机顺序连点')
        return plan

    # ---------------------------------------------------------------- 技能冷却识别
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

    def battle_skill_cooling(self, index: int, image=None) -> bool:
        """技能是否冷却中（防抖后）"""
        return self.battle_skill_state(index, image) == SKILL_COOLING

    def battle_skills_state(self, image=None) -> dict:
        """所有技能位的状态（防抖后）{下标: ready/cooling/unknown}"""
        return self.battle_states(image)

    def battle_skills_ready(self, image=None) -> list:
        """冷却好的技能下标列表（升序）"""
        states = self.battle_skills_state(image)
        return [index for index in sorted(states) if states[index] == SKILL_READY]

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

    def battle_click_skill_if_ready(self, index: int) -> bool:
        """
        技能冷却好就点一次（内部先截图再判断）；冷却中 / unknown 不点

        :param index: BATTLE_SKILL_REGIONS 的下标，0 = 技能1
        :return: 点了返回 True
        """
        self.screenshot()
        state = self.battle_skill_state(index)
        if state != SKILL_READY:
            logger.info(f'技能{index + 1}状态 {state}，本次不点')
            return False
        self.battle_click_skill(index)
        return True

    def battle_simple_loop(self,
                           stop_check: Callable[[], bool] = None,
                           timeout: float = None,
                           groups_max: int = None,
                           use_plan: bool = True) -> bool:
        """
        循环放技能，直到：
        - stop_check() 返回 True（比如出现「过关奖励」面板）-> 返回 True
        - 超时 / 达到轮数上限 -> 返回 False

        放技能的方式：
        - `use_plan=True`（默认）：开战前识别 7 个技能位带了哪个技能，
          按任务配置里选的预设（默认系统预设）优先级放，`respect_cooldown` 开着就只点冷却好的；
        - `use_plan=False`：退回老行为（技能 + 普攻各用一次，顺序随机，每按钮连点 3 次）。

        注意：技能栏内容在一场战斗内不会变，所以计划只在开战时算一次。

        :param stop_check: 无参函数，通常内部自己截图判断
        :param timeout: 秒
        :param groups_max: 最多打多少轮
        :param use_plan: 是否按优先级计划放技能（默认 True）
        """
        plan: List[PlanEntry] = []
        if use_plan:
            # 现截一张再识别：技能栏刚进战斗时可能还在动画，用旧图容易认错
            self.screenshot()
            plan = self.battle_plan()
            if not plan:
                logger.warning('技能位一个都没认出来，本场退回随机顺序连点')
                use_plan = False

        timer = Timer(timeout).start() if timeout else None
        groups = 0
        while 1:
            if groups_max is not None and groups >= groups_max:
                logger.warning(f'战斗达到轮数上限 {groups_max}，结束')
                return False
            if use_plan:
                self.battle_play_plan(plan)
            else:
                self.battle_one_group()
            groups += 1
            logger.info(f'已释放 {groups} 轮')
            if stop_check and stop_check():
                logger.info(f'战斗结束（共 {groups} 轮）')
                return True
            if timer and timer.reached():
                logger.warning(f'战斗超时（{timeout}s，共 {groups} 轮）')
                return False
