# This Python file uses the following encoding: utf-8
"""
战斗能力（SimpleBattle）：目标锁定 + 所有战斗执行能力

**战斗执行（点技能/冷却/识别/按预设攻击）已经搬到 `tasks/Battle/attack.py`**，
这里只保留「目标锁定」这类纯 UI 操作（依赖 GameUi 的 OCR/点击素材），
然后把 `BattleAttack` 混进来，任务照旧 `self.battle_attack()` 就能用。

- 目标锁定：`ui_lock_target()` 点右侧竖排「目标」按钮、`ui_target_name()` 读锁定目标名
- 战斗执行（见 tasks/Battle/attack.py）：
  - 单次点击：`battle_click_once / battle_click_attack / battle_click_skill`（7 个技能位）
  - 技能冷却：`battle_skill_state()`（ready / cooling / unknown）、`battle_skill_ready()`、
    `battle_skills_ready()`、`battle_click_skill_if_ready()`
  - 技能位识别：`battle_slot_skills()` / `battle_log_slot_skills()`
  - 释放计划：`battle_plan()`（识别 + 预设优先级 + 禁用 -> 点击顺序）、
    `battle_play_plan()`（按计划放一轮，可选只点冷却好的）
  - **按预设攻击（对外只暴露这一个入口）**：`battle_attack(times=1, preset='')`
  - 循环放技能：`battle_simple_loop()`

目标锁定是主页面通用能力：「目标」按钮在右侧竖排
（要收起右上角菜单 `ui_close_menu` 后才露出），
WorldBoss 与域外迷窟都用它，素材与规则统一放在 GameUi 里，任务只调方法。
"""
from tasks.Battle.attack import BattleAttack
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class SimpleBattle(BattleAttack, BaseTask, GameUiAssets):
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
