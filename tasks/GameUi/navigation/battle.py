# This Python file uses the following encoding: utf-8
"""
战斗通用能力（SimpleBattle）：

- 目标锁定：`ui_lock_target()` 点右侧竖排「目标」按钮、`ui_target_name()` 读锁定目标名
- 单次点击：`battle_click_once / battle_click_attack / battle_click_skill`（7 个技能位）
- 技能冷却：`battle_skill_state()`（ready / cooling / unknown）、`battle_skill_ready()`、
  `battle_skills_ready()`、`battle_click_skill_if_ready()`（原理见 navigation/battle_skill.py）
- 循环放技能：`battle_simple_loop()` 按组随机顺序释放现有技能 + 普通攻击

按钮区域来自真机实测（1280x720），单位是 (x, y, w, h)；
技能栏在右下角，普通攻击是右下角那个大按钮。
**迷窟（域外迷窟）里实测这套区域坐标逐个吻合**，所以直接复用，任务不用再录一遍素材。

目标锁定同样是主页面通用能力：「目标」按钮在右侧竖排
（要收起右上角菜单 `ui_close_menu` 后才露出），
WorldBoss 与域外迷窟都用它，素材与规则统一放在 GameUi 里，任务只调方法。

`battle_simple_loop()` 的行为：
- 一组 = 现有技能 + 普通攻击，**每个按钮都用一次**，顺序每组随机
- 每个按钮点击时：在按钮区域内**随机取点**，连点 BATTLE_CLICK_TIMES 次（默认 3 次），
  每次间隔 BATTLE_CLICK_INTERVAL（默认 200ms）
- 不同按钮之间间隔 BATTLE_SKILL_INTERVAL（默认 300ms）
- 循环打组，直到 stop_check 命中 / 超时 / 达到组数上限
"""
import random
from typing import Callable

from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.navigation.battle_skill import (SKILL_COOLDOWN_RATIO, SKILL_READY,
                                                  SkillCooldownJudge, skill_region_score)
from tasks.base_task import BaseTask


class SimpleBattle(BaseTask, GameUiAssets):
    # 全部技能位（真机实测，x,y,w,h）；目前 7 个技能位都是实际技能
    BATTLE_SKILL_REGIONS_ALL = (
        (995, 588, 47, 42),    # 技能位1
        (1034, 490, 50, 41),   # 技能位2
        (1125, 442, 53, 44),   # 技能位3
        (887, 587, 55, 44),    # 技能位4
        (923, 483, 46, 46),    # 技能位5
        (998, 391, 48, 45),    # 技能位6
        (1092, 337, 56, 42),   # 技能位7
    )
    # 当前实际存在的技能按钮：7 个位都有技能（2026-10-23 实机确认）
    BATTLE_SKILL_REGIONS = BATTLE_SKILL_REGIONS_ALL
    # 兼容旧配置：以前有空技能位时用它决定是否点空位；7 个位都是实际技能后不再生效
    BATTLE_CLICK_EMPTY_SKILLS = False
    # 普通攻击按钮区域 (x, y, w, h)
    BATTLE_ATTACK_REGION = (1155,605,64,46)

    # 技能冷却判定区域 (x, y, w, h)：以各技能按钮为中心，52x54；
    # 技能1 (991,582,52,54) 是真机标定的，其余与基准素材的 roiFront 一致
    BATTLE_SKILL_CHECK_REGIONS = {
        0: (991, 582, 52, 54),
        1: (1033, 484, 52, 54),
        2: (1125, 437, 52, 54),
        3: (888, 582, 52, 54),
        4: (920, 479, 52, 54),
        5: (996, 386, 52, 54),
        6: (1094, 331, 52, 54),
    }
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

    # 每个按钮连点次数与间隔（秒）
    BATTLE_CLICK_TIMES = 3
    BATTLE_CLICK_INTERVAL = 0.2
    # 不同按钮之间的间隔（秒）
    BATTLE_SKILL_INTERVAL = 0.3

    # ---------------------------------------------------------------- 目标锁定
    # 目标名识别：灰度放大倍数与置信度下限（彩色描边字要整行放大才读得出来）
    TARGET_NAME_SCALES = (2, 3)
    TARGET_NAME_SCORE = 0.5

    def ui_target_name(self, scales: tuple = None, min_score: float = None) -> str:
        """
        读取当前锁定目标的名称，读不到返回空串

        目标名是带描边的彩色字，规则默认的「检测框 + 拼串」在真机上经常一个框都检不出来
        （WorldBoss 实测「妖化蟹将」读成空串，或拆成两段各读错一个字），
        所以走 GameUi.ocr_color_name（ROI 转灰度 -> 放大 -> 整行识别，取置信度高的一份）。
        """
        return self.ocr_color_name(self.O_TARGET_NAME,
                                   scales=scales or self.TARGET_NAME_SCALES,
                                   min_score=min_score if min_score is not None
                                   else self.TARGET_NAME_SCORE)

    def ui_lock_target(self, interval: float = None) -> bool:
        """
        点右侧竖排的「目标」按钮，锁定/切换到下一个目标

        必须先收起右上角菜单（ui_close_menu），否则「目标」那一列被菜单图标盖住。

        :param interval: 点击间隔（限频）
        :return: 点到了返回 True
        """
        self.screenshot()
        # 点图标下方的「目标」文字比点图标稳（图标区域小，识别容易抖）
        return self.appear_then_click(self.I_TARGET_BUTTON,
                                      action=self.C_TARGET_BUTTON_CLICK,
                                      interval=interval)

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
        """
        buttons = self.battle_buttons()
        random.shuffle(buttons)
        logger.info(f'释放一组技能（随机顺序）：{[b for b in buttons]}')
        for region in buttons:
            self.battle_click_button(region)
            self.device.sleep(self.BATTLE_SKILL_INTERVAL)

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

    def battle_skill_state(self, index: int, image=None) -> str:
        """技能状态：ready / cooling / unknown（缺基准素材时为 unknown）"""
        return self.battle_skill_judge().state(self._battle_image(image), index)

    def battle_skill_ready(self, index: int, image=None) -> bool:
        """技能是否冷却好"""
        return self.battle_skill_state(index, image) == SKILL_READY

    def battle_skill_cooling(self, index: int, image=None) -> bool:
        """技能是否冷却中"""
        return self.battle_skill_judge().is_cooling(self._battle_image(image), index)

    def battle_skills_state(self, image=None) -> dict:
        """所有技能位的状态 {下标: ready/cooling/unknown}"""
        return self.battle_skill_judge().states(self._battle_image(image))

    def battle_skills_ready(self, image=None) -> list:
        """冷却好的技能下标列表（升序）"""
        states = self.battle_skills_state(image)
        return [index for index in sorted(states) if states[index] == SKILL_READY]

    def battle_log_skill_states(self, image=None) -> dict:
        """输出一行「每个技能位的状态 + 当前分/基准分」，真机校准用"""
        image = self._battle_image(image)
        judge = self.battle_skill_judge()
        states = judge.states(image)
        text = ' '.join(f'{index + 1}:{states[index]}({judge.ratio_of(image, index):.2f})'
                        for index in sorted(states))
        logger.info(f'技能冷却状态 {text}')
        return states

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
                           groups_max: int = None) -> bool:
        """
        循环打组（每组内顺序随机），直到：
        - stop_check() 返回 True（比如出现「过关奖励」面板）-> 返回 True
        - 超时 / 达到组数上限 -> 返回 False

        :param stop_check: 无参函数，通常内部自己截图判断
        :param timeout: 秒
        :param groups_max: 最多打多少组
        """
        timer = Timer(timeout).start() if timeout else None
        groups = 0
        while 1:
            if groups_max is not None and groups >= groups_max:
                logger.warning(f'战斗达到组数上限 {groups_max} 组，结束')
                return False
            self.battle_one_group()
            groups += 1
            logger.info(f'已释放 {groups} 组')
            if stop_check and stop_check():
                logger.info(f'战斗结束（共 {groups} 组）')
                return True
            if timer and timer.reached():
                logger.warning(f'战斗超时（{timeout}s，共 {groups} 组）')
                return False
