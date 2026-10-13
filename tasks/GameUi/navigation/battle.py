# This Python file uses the following encoding: utf-8
"""
战斗通用能力（SimpleBattle）：

- 目标锁定：`ui_lock_target()` 点右侧竖排「目标」按钮、`ui_target_name()` 读锁定目标名
- 单次点击：`battle_click_once / battle_click_attack / battle_click_skill`
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
from tasks.base_task import BaseTask


class SimpleBattle(BaseTask, GameUiAssets):
    # 全部技能位（真机实测，x,y,w,h）；其中 3 个是空技能位（未解锁），不能点
    BATTLE_SKILL_REGIONS_ALL = (
        (995, 588, 47, 42),
        (1034, 490, 50, 41),
        (1125, 442, 53, 44),
        (887, 587, 55, 44),
        (923, 483, 46, 46),    # 空
        (998, 391, 48, 45),    # 空
        (1092, 337, 56, 42),   # 空
    )
    # 当前实际存在的技能按钮（空技能位不点）
    BATTLE_SKILL_REGIONS = (
        (995, 588, 47, 42),    # 技能位1
        (1034, 490, 50, 41),   # 技能位2
        (1125, 442, 53, 44),   # 技能位3
        (887, 587, 55, 44),    # 技能位4
    )
    # 是否也点空技能位（不同设备技能不同：有的设备有空位，点了没反应甚至可能点到别的按钮）
    BATTLE_CLICK_EMPTY_SKILLS = False
    # 普通攻击按钮区域 (x, y, w, h)
    BATTLE_ATTACK_REGION = (1155,605,64,46)

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
