# This Python file uses the following encoding: utf-8
"""
战斗能力（SimpleBattle）：目标锁定 + 所有战斗执行能力

**战斗执行（点技能/冷却/识别/按预设攻击）已经搬到 `tasks/Battle/attack.py`**，
这里只保留「目标锁定」这类纯 UI 操作（依赖 GameUi 的 OCR/点击素材），
然后把 `BattleAttack` 混进来，任务照旧 `self.battle_attack()` 就能用。

- 目标锁定：`ui_lock_target()` 点右侧竖排「目标」按钮、`ui_target_name()` 读锁定目标名
- 锁定目标把关（通用）：
  - `ui_target_locked()` 是否已锁定任意目标（血条框雕花模板匹配）
  - `ui_target_action()` 右下角动作按钮：attack / gather / interact / other（盘面色）
  - `ui_target_attackable()` 动作按钮是否为「攻击」
  - `ui_ensure_target_locked(target_name=...)` 收起菜单 -> 锁目标 -> 校验按钮/名称，
    不符合就点「目标」切换，直到满足或超时（场景 1 单目标 / 场景 2 多目标指定名称）
  - `battle_attack_until_target_lost()` 按预设一直打，目标丢失或按钮不再是「攻击」时停止
  - `ui_lock_and_attack(target_name=...)` 上面两步的合并入口
- 战斗执行（见 tasks/Battle/attack.py）：
  - 单次点击：`battle_click_once / battle_click_attack / battle_click_skill`（7 个技能位）
  - 技能冷却：`battle_skill_state()`（ready / cooling / unknown）、`battle_skill_ready()`、
    `battle_skills_ready()`、`battle_click_skill_if_ready()`
  - 技能位识别：`battle_slot_skills()` / `battle_log_slot_skills()`
  - 释放计划：`battle_plan()`（识别 + 预设优先级 + 禁用 -> 点击顺序）、
    `battle_play_plan()`（按计划放一轮，可选只点冷却好的）
  - **按预设攻击（对外只暴露这一个入口）**：`battle_attack(times=1, preset='')`
  - 队列式释放（双击 + 目标/冷切校验）：`battle_skill_loop(slots, check_stop=..., target_check=...)`（仙府/战斗试打共用）
  - 循环放技能：`battle_simple_loop()`

目标锁定是主页面通用能力：「目标」按钮在右侧竖排
（要收起右上角菜单 `ui_close_menu` 后才露出），
WorldBoss 与域外迷窟都用它，素材与规则统一放在 GameUi 里，任务只调方法。
"""
from module.base.timer import Timer
from module.logger import logger
from tasks.Battle.attack import BattleAttack
from tasks.Battle.target_action import (ACTION_ATTACK, ACTION_NAMES_ZH,
                                        classify_action_button, is_attackable)
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.core.ocr import OcrHelpers
from tasks.base_task import BaseTask


class SimpleBattle(BattleAttack, BaseTask, GameUiAssets):
    # 目标名识别：灰度放大倍数与置信度下限（彩色描边字要整行放大才读得出来）
    TARGET_NAME_SCALES = (2, 3)
    TARGET_NAME_SCORE = 0.5

    # 锁定目标的切换节奏：每次点完「目标」停一下，等界面刷新
    TARGET_LOCK_SWITCH_INTERVAL = 0.5
    # 最多点几次「目标」按钮，超了就放弃
    TARGET_LOCK_MAX_SWITCH = 10
    # 锁定目标的整体超时（秒）
    TARGET_LOCK_TIMEOUT = 30

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

    # ---------------------------------------------------------------- 锁定目标把关
    def ui_target_locked(self, image=None) -> bool:
        """
        当前是否锁定了任意目标（血条框右侧雕花做模板匹配）

        :param image: 不传就用当前截图（没有就现截）
        """
        image = self._battle_image(image)
        if image is None:
            return False
        return self.I_TARGET_LOCKED.match(image)

    def ui_target_action(self, image=None) -> str | None:
        """
        右下角动作按钮的类别：attack / gather / interact / other

        红盘「攻击」= 可攻击；绿盘「采集」/ 蓝盘「交互」= 不可攻击。
        按钮不可见（菜单/弹窗里）返回 None。

        :param image: 不传就用当前截图（没有就现截）
        """
        return classify_action_button(self._battle_image(image))

    def ui_target_attackable(self, image=None) -> bool:
        """
        当前是否锁定了一个可攻击目标（已锁定 且 动作按钮为「攻击」）

        注意：没锁目标时按钮也可能是红色「攻击」，必须两件事一起判断。
        """
        image = self._battle_image(image)
        return (self.ui_target_locked(image=image)
                and is_attackable(self.ui_target_action(image=image)))

    @staticmethod
    def _target_name_list(target_name) -> list:
        """把配置里的目标名（str / 逗号分隔 / list）规整成非空名称列表"""
        if not target_name:
            return []
        if isinstance(target_name, str):
            target_name = target_name.split(',')
        return [str(name).strip() for name in target_name if str(name).strip()]

    def ui_ensure_target_locked(self, target_name=None, action: str = ACTION_ATTACK,
                                max_switch: int = None, timeout: float = None,
                                name_scales: tuple = None, name_score: float = None,
                                close_menu: bool = True) -> bool:
        """
        锁定目标并把关：确保「已锁定 + 动作按钮符合 +（可选）目标名匹配」

        流程：收起右上角菜单 -> 截图判断是否已锁定；不满足条件就点「目标」按钮
        切换到下一个目标，直到满足或切换次数/超时用尽。

        两种场景：
        1. 地图里只有一个可锁定目标：`ui_ensure_target_locked()`，
           只要求锁到目标且按钮是「攻击」，不校验名称；
        2. 一个地方有多个目标、指定了名称：`ui_ensure_target_locked(target_name='妖化蟹将')`，
           先确认按钮是「攻击」，再 OCR 目标名，对不上就继续切目标。

        调用方负责先把角色移动到目标附近；本方法只做锁定与校验。

        :param target_name: 指定目标名（str / 逗号分隔 / list），None = 不校验名称
        :param action: 期望的动作，默认 attack（可攻击）；None = 不限制动作
        :param max_switch: 最多点几次「目标」按钮（默认 TARGET_LOCK_MAX_SWITCH）
        :param timeout: 超时时间（秒，默认 TARGET_LOCK_TIMEOUT）
        :param name_scales: 目标名 OCR 放大倍数（默认 TARGET_NAME_SCALES）
        :param name_score: 目标名 OCR 置信度下限（默认 TARGET_NAME_SCORE）
        :param close_menu: 是否先收起右上角菜单（「目标」按钮会被菜单盖住）
        :return: 锁定成功 True
        """
        names = self._target_name_list(target_name)
        max_switch = self.TARGET_LOCK_MAX_SWITCH if max_switch is None else int(max_switch)
        timeout = self.TARGET_LOCK_TIMEOUT if timeout is None else float(timeout)
        need_text = ACTION_NAMES_ZH.get(action, action) if action else ''

        logger.hr('Lock target', level=2)
        if close_menu:
            self.ui_close_menu()

        timer = Timer(timeout).start()
        switches = 0
        while True:
            # 长时间轮询，清掉卡死/连点记录，避免被框架判成卡死
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            self.screenshot()
            image = self.device.image

            if self.ui_target_locked(image=image):
                current = self.ui_target_action(image=image)
                # action=None 表示只要求锁定，不限制按钮动作
                if action is None or current == action:
                    if not names:
                        logger.info('Target locked' if action is None
                                    else f'Target locked, action is {need_text}')
                        return True
                    name = self.ui_target_name(scales=name_scales, min_score=name_score)
                    if any(OcrHelpers.ocr_name_match(name, expected) for expected in names):
                        logger.info(f'Target locked and matched: {name}')
                        return True
                    logger.info(f'Target [{name or "?"}] not in {names}, switch target')
                else:
                    logger.info(f'Target action is {ACTION_NAMES_ZH.get(current, current)}, '
                                f'need {need_text}, switch target')
            else:
                logger.info('No target locked, click target button')

            if switches >= max_switch:
                logger.warning(f'Target lock failed after {max_switch} switches')
                return False
            if timer.reached():
                logger.warning(f'Target lock timeout after {timeout}s')
                return False

            switches += 1
            # 用当前这张截图点按钮，不重复截图；间隔靠 sleep 保证
            self.appear_then_click(self.I_TARGET_BUTTON,
                                   action=self.C_TARGET_BUTTON_CLICK)
            self.device.sleep(self.TARGET_LOCK_SWITCH_INTERVAL)

    def ui_target_lost_check(self, action: str = ACTION_ATTACK):
        """
        生成 battle_attack_loop 的 stop_check：目标丢失或动作按钮不再是期望动作 -> True

        用最近一张截图（self.device.image）判断，不额外截图：
        攻击循环每轮本来就会截图，最多晚一个循环（约 0.2~1s）发现目标消失。

        :param action: 期望保持的动作，默认 attack；None = 只要求目标还在锁定
        """
        def _check() -> bool:
            image = self.device.image if self.exist_image() else None
            if image is None:
                return False
            if not self.ui_target_locked(image=image):
                logger.info('Target lost, stop attacking')
                return True
            if action is None:
                return False
            current = self.ui_target_action(image=image)
            if current != action:
                logger.info(f'Target action changed to '
                            f'{ACTION_NAMES_ZH.get(current, current)}, stop attacking')
                return True
            return False

        return _check

    def battle_attack_until_target_lost(self, action: str = ACTION_ATTACK, **kwargs) -> None:
        """
        锁定目标后按预设一直攻击，目标丢失 / 动作按钮不再是「攻击」时自动停止

        用法（两个场景通用）：
            if not self.ui_ensure_target_locked():
                return False
            self.battle_attack_until_target_lost()
            return True

        :param action: 期望持续的动作（默认 attack）
        :param kwargs: 透传给 battle_attack_loop（preset / clicks_per_skill / include_attack ...）
        """
        kwargs.pop('stop_check', None)
        self.battle_attack_loop(stop_check=self.ui_target_lost_check(action=action), **kwargs)

    def ui_lock_and_attack(self, target_name=None, action: str = ACTION_ATTACK,
                           attack_kwargs: dict = None, **lock_kwargs) -> bool:
        """
        锁定目标并攻击到目标丢失（场景 1/2 的统一入口）

        :param target_name: 指定目标名，None = 不校验（单目标场景）
        :param action: 期望动作，默认 attack
        :param attack_kwargs: 透传给 battle_attack_loop 的参数
        :param lock_kwargs: 透传给 ui_ensure_target_locked 的参数（max_switch / timeout ...）
        :return: 锁定成功并完成攻击返回 True；没锁到目标返回 False
        """
        if not self.ui_ensure_target_locked(target_name=target_name, action=action, **lock_kwargs):
            return False
        self.battle_attack_until_target_lost(action=action, **(attack_kwargs or {}))
        return True
