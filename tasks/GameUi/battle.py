# This Python file uses the following encoding: utf-8
"""
简单战斗系统（SimpleBattle）：

主页面/副本里的技能栏（现有 4 个技能 + 1 个普通攻击；空技能位不点）循环释放：

- 一组 = 现有技能 + 普通攻击，**每个按钮都用一次**，顺序每组随机
- 每个按钮点击时：在按钮区域内**随机取点**，连点 BATTLE_CLICK_TIMES 次（默认 3 次），
  每次间隔 BATTLE_CLICK_INTERVAL（默认 200ms）
- 不同按钮之间间隔 BATTLE_SKILL_INTERVAL（默认 300ms）
- `battle_simple_loop()` 循环打组，直到 stop_check 命中 / 超时 / 达到组数上限

按钮区域来自真机实测（1280x720），单位是 (x, y, w, h)；
技能栏在右下角，普通攻击是右下角那个大按钮。
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
    BATTLE_ATTACK_REGION = (1146, 597, 82, 60)

    # 每个按钮连点次数与间隔（秒）
    BATTLE_CLICK_TIMES = 3
    BATTLE_CLICK_INTERVAL = 0.2
    # 不同按钮之间的间隔（秒）
    BATTLE_SKILL_INTERVAL = 0.3

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
