# This Python file uses the following encoding: utf-8
"""
副本玩法 - 通用逻辑（DungeonBase）

所有具体副本共用的能力都放这里；具体副本（tasks/Dungeon/dungeons/*.py）继承
DungeonBase，只实现自己的差异，避免每个副本各写一套。

新增副本：
- 继承 DungeonBase，覆盖 name/FIELD/MAP_NAME 等，实现 run_once()
- 「准备主页面 / 外地图 / 治疗修理」等直接调用本类方法即可
"""
import time

import cv2
import numpy as np

from module.atom.click import RuleClick
from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError)
from module.logger import logger
from tasks.Battle.target_action import ACTION_ATTACK
from tasks.Dungeon.assets import DungeonAssets
from tasks.Dungeon.counts import (FIELD_NAMES, config_counts,
                                  dungeon_name_matches, parse_dungeon_rows,
                                  parse_privilege_count)
from tasks.Dungeon.runner import DungeonAborted
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_activity_dungeon, page_main


class DungeonBase(GameUi, DungeonAssets):
    """副本通用基类：具体副本继承它并实现 run()"""

    # 副本名（子类覆盖，仅用于日志/提示）
    name: str = '副本'

    # 左侧列表扫描：3 屏拿全（首屏 + 两次滚动），每屏滚约 3~4 行
    DUNGEON_SCROLL_START = (300, 520)
    DUNGEON_SCROLL_END = (300, 280)
    DUNGEON_SCAN_MAX_SCREENS = 3
    # 打开副本页后等列表动画结束，否则首屏 OCR 会漏掉下面几行
    DUNGEON_PAGE_SETTLE = 0.8

    # 左侧列表每行的点击位置（1280x720，真机实测 5 行）
    DUNGEON_ROW_X = 300
    # 选中行黄色描边：点击后用它的中心确认选中的就是目标行
    DUNGEON_SELECTED_ROW_TOLERANCE = 35
    DUNGEON_SELECTED_MIN_ROW_PIXELS = 100
    DUNGEON_CLICK_SETTLE = 0.6
    # 列表滑动有回弹，等它静止后再 OCR / 点击
    DUNGEON_SCROLL_SETTLE = 1.5
    # 右侧选中特征：真实选中都是 1.0；未录制副本最高只有 ~0.82，
    # 所以阈值提到 0.9 并把「第一名与第二名」拉开，避免把试炼误认成灵岛
    DUNGEON_FEATURE_MIN_SCORE = 0.9
    DUNGEON_FEATURE_MIN_MARGIN = 0.05

    def run(self) -> None:
        """任务入口由 ScriptTask 实现；具体副本只实现 run_once()。"""
        raise NotImplementedError(f'{type(self).__name__}.run is not implemented')

    def run_once(self) -> bool:
        """
        完成一次本副本（进图 -> 战斗/结算），成功返回 True。

        由具体副本类实现；调用前调度器只确认了剩余次数，具体副本从主地图
        自行进入，不依赖副本页的选中状态（选中只在重置时使用）。
        """
        raise NotImplementedError(f'{type(self).__name__}.run_once is not implemented')

    def battle_task_preset(self) -> str:
        """副任务的战斗预设读嵌套的 battle_config 组（共享实现只读任务根节点）"""
        battle = getattr(self.config.dungeon, 'battle_config', None)
        return str(getattr(battle, 'battle_preset', '') or '')

    # ---------------------------------------------------------------- 死亡处理
    def handle_death(self) -> None:
        """
        非副本地图死亡：复活后继续当前流程，不整任务重跑。

        副本地图里不会弹死亡框，角色死亡会自动离开副本，由
        dungeon_check_in_map / wait_location 检测后跳过该副本。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        logger.warning(f'[{self.name}] Character died outside dungeon, revive and continue')
        self.revive()

    def dungeon_notify_uncleared(self) -> None:
        """副本内死亡/自动离开：推送「<副本名>未通关」，然后跳过该副本剩余次数"""
        title = f'{self.name}未通关'
        content = (f'<{self.config.config_name}> {self.name}副本未通关：'
                   f'角色在副本中死亡并自动离开，跳过该副本剩余次数')
        logger.warning(content)
        try:
            self.config.notifier.push(title=title, content=content)
        except Exception as e:
            logger.warning(f'Notify uncleared dungeon failed: {e}')
        raise DungeonAborted(title)

    def dungeon_check_in_map(self, map_name: str) -> None:
        """校验还在副本地图里；已经自动离开则视为死亡未通关"""
        location = self.map_current_location()
        if location is None:
            return
        if not self.map_name_match(location[0], map_name):
            logger.warning(f'Character left dungeon map [{map_name}], now {location}')
            self.dungeon_notify_uncleared()

    # ---------------------------------------------------------------- 副本次数
    def dungeon_open_page(self, timeout: int = 60) -> bool:
        """
        打开「活动-副本」页，并把左侧列表复位到顶部。

        切换「推荐 / 副本」子 tab **不会**复位列表（会保留上次滚到的位置），
        必须先关掉整个活动弹窗回到主页面，再重新进副本页。
        """
        logger.hr(f'[{self.name}] 打开副本页')
        self.ui_reset_current_page()
        # 当前在活动弹窗里时，ui_goto(page_main) 会点右上角 X 关掉整个弹窗
        if not self.ui_goto(page_main, timeout=timeout):
            logger.warning('Main page does not appear')
            return False
        if not self.ui_goto(page_activity_dungeon, timeout=timeout):
            logger.warning('Activity dungeon page does not appear')
            return False
        self.device.sleep(self.DUNGEON_PAGE_SETTLE)
        self.screenshot()
        return self.appear(self.I_PAGE_ACTIVITY_DUNGEON)

    # ---------------------------------------------------------------- 外地图入口
    # 入口点位来自真机实测；outer/entry 为 None 表示还没录入，暂不进入。
    # 琉璃幻境入口在忘川；仙居福地暂不实现。天渊灵岛/至尊巅峰暂不实现。
    DUNGEON_OUTER_ENTRIES = {
        'shayu_multi': {'base': '牧野', 'corner': 'top_right',
                        'entry': 'shayu_outer_entry', 'outer': '沙狱外'},
        'shayu_solo': {'base': '牧野', 'corner': 'top_right',
                       'entry': 'shayu_outer_entry', 'outer': '沙狱外'},
        'diyuling_multi': {'base': '万剑冢', 'corner': 'bottom_right',
                           'entry': 'diyuling_outer_entry', 'outer': '帝玉岭外'},
        'diyuling_solo': {'base': '万剑冢', 'corner': 'bottom_right',
                          'entry': 'diyuling_outer_entry', 'outer': '帝玉岭外'},
        'liuli_huanjing': {'base': '忘川', 'corner': None,
                           'entry': None, 'outer': None},
    }
    DUNGEON_ENTER_TIMEOUT = 60

    def dungeon_enter_shayu_outer(self) -> bool:
        """牧野 -> 沙狱外：小地图视口右上角，点 (846,515,27,26)"""
        return self.dungeon_enter_outer('shayu_multi')

    def dungeon_enter_diyuling_outer(self) -> bool:
        """万剑冢 -> 帝玉岭外：小地图视口右下角，点 (800,500,27,26)"""
        return self.dungeon_enter_outer('diyuling_multi')

    def dungeon_enter_outer(self, field: str) -> bool:
        """按配置字段进入副本外地图；没有外地图的（琉璃幻境）只确保在主地图"""
        info = self.DUNGEON_OUTER_ENTRIES.get(field)
        if info is None:
            logger.warning(f'No outer map recorded for dungeon [{field}]')
            return False
        if not info.get('outer'):
            # 入口直接在主地图：琉璃幻境在忘川
            logger.hr(f'[{self.name}] 准备主地图 {info["base"]}')
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=60):
                logger.warning('Main page does not appear')
                return False
            self.map_close_main_popup()
            location = self.map_current_location()
            if location and self.map_name_match(location[0], info['base']):
                logger.info(f'Already in base map [{info["base"]}]')
                return True
            return self.map_teleport(info['base'])
        if not info.get('entry'):
            logger.warning(f'Outer map entrance not recorded for dungeon [{field}] '
                           f'(base map: {info.get("base")})')
            return False
        click_rule = getattr(self, f'C_{info["entry"].upper()}', None)
        if click_rule is None:
            logger.warning(f'Missing click rule [{info["entry"]}] for dungeon [{field}]')
            return False
        return self._dungeon_enter_outer_map(info['base'], info['corner'],
                                             click_rule, info['outer'])

    def _dungeon_enter_outer_map(self, base: str, corner: str, click_rule,
                                 target: str, timeout: int = None) -> bool:
        """
        进入副本外地图：确保在 base 地图 -> 开小地图 -> 视口移到指定角落
        -> 点入口 -> 关小地图 -> 等地点名变成 target。
        """
        logger.hr(f'[{self.name}] 进入 {target}')
        timeout = timeout if timeout is not None else self.DUNGEON_ENTER_TIMEOUT
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            logger.warning('Main page does not appear')
            return False
        self.map_close_main_popup()
        location = self.map_current_location()
        if location and self.map_name_match(location[0], target):
            logger.info(f'Already in [{target}], no need to re-enter')
            return True
        if location is None or not self.map_name_match(location[0], base):
            logger.info(f'Teleport to base map [{base}]')
            if not self.map_teleport(base):
                logger.warning(f'Unable to teleport to [{base}]')
                return False
            self.device.sleep(1.0)
            self.ui_reset_current_page()

        if not self.map_open_minimap():
            return False
        try:
            if not self.map_pan_to_corner(corner,
                                           max_drags=self.DUNGEON_PAN_MAX_DRAGS):
                return False
            logger.info(f'Click {target} entrance at {click_rule.roi_front}')
            self.click(click_rule)
            self.device.click_record_clear()
            self.device.sleep(0.5)
        finally:
            self.map_close_minimap()

        timer = Timer(timeout).start()
        while not timer.reached():
            self.device.stuck_record_clear()
            location = self.map_current_location()
            if location and self.map_name_match(location[0], target):
                logger.info(f'Entered [{target}] at {location[1]},{location[2]}')
                self.ui_reset_current_page()
                return True
            self.device.sleep(1)
        logger.warning(f'Enter [{target}] timeout, current {location}')
        return False

    # ---------------------------------------------------------------- 万事通治疗
    DUNGEON_HEALER_NAME = '万事通'
    DUNGEON_HEAL_OPTION = '治疗伤势'
    DUNGEON_HEALER_DIALOG_TIMEOUT = 30
    # 等这么久还没对话框，就检查目标是不是万事通，点攻击键交互
    DUNGEON_HEALER_INTERACT_AFTER = 5
    DUNGEON_HEALER_MAX_INTERACT = 3
    DUNGEON_HEAL_RESULT_TIMEOUT = 10
    # 副本外地图的小地图窗口比 Yuwai/SilverGiftBox 窄：NPC 列表文字在 x≈850~990，
    # 公共默认区域 (950,180,170,420) 会切掉名字前半截
    DUNGEON_NPC_LIST_REGION = (845, 165, 170, 480)

    def dungeon_heal(self, retries: int = 1) -> bool:
        """
        找万事通 -> 打开对话 -> 点「治疗伤势」。
        :return: 治疗成功 True；治疗失败（如冷却中）或没找到对话框 False
        """
        logger.hr(f'[{self.name}] 治疗伤势')
        for attempt in range(retries + 1):
            if not self.dungeon_find_healer():
                logger.warning(f'Healer not found (attempt {attempt + 1}/{retries + 1})')
                continue
            if not self.dungeon_click_heal_option():
                logger.warning(f'Heal option not found (attempt {attempt + 1}/{retries + 1})')
                continue
            return self.dungeon_wait_heal_result()
        logger.warning('Heal failed after retries')
        return False

    def dungeon_repair_equipment(self) -> bool:
        """副本准备：复用背包「随身功能 -> 修理装备」的现有流程"""
        logger.hr(f'[{self.name}] 修理装备')
        return self.bag_repair_equipment()

    def dungeon_find_healer(self) -> bool:
        """走到万事通身边并打开对话"""
        logger.info(f'Find healer [{self.DUNGEON_HEALER_NAME}]')
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            logger.warning('Main page does not appear')
            return False
        self.map_close_main_popup()
        location = self.map_current_location()

        # 已录点位的副本外地图：小地图视口左下角直接点万事通
        healer_rules = (
            ('沙狱外', self.C_SHAYU_HEALER_NPC),
            ('帝玉岭外', self.C_DIYULING_HEALER_NPC),
            ('忘川', self.C_LIULI_HEALER_NPC),
        )
        click_rule = None
        if location:
            for map_name, rule in healer_rules:
                if self.map_name_match(location[0], map_name):
                    click_rule = rule
                    break

        if click_rule is not None:
            if not self.map_open_minimap():
                return False
            try:
                if not self.map_pan_to_corner('bottom_left',
                                              max_drags=self.DUNGEON_PAN_MAX_DRAGS):
                    # 忘川这种大地图箭头判断不可靠，视口可能已经正确，继续点击
                    logger.warning('Minimap pan to bottom_left failed, click healer anyway')
                logger.info(f'Click healer position {click_rule.roi_front}')
                self.click(click_rule)
                self.device.click_record_clear()
                self.device.sleep(0.5)
            finally:
                self.map_close_minimap()
            # 忘川等大地图自动寻路到万事通可能比较久，给 30s 等走到
            logger.info('Wait healer walk arrive (up to 30s)')
            self.map_wait_walk_arrive(stable_time=2, timeout=30)
        else:
            # 其它地图：小地图右侧 NPC 列表找「万事通」
            logger.info(f'Use minimap NPC list to find [{self.DUNGEON_HEALER_NAME}]')
            if not self.map_open_minimap_and_click_npc(
                    self.DUNGEON_HEALER_NAME, region=self.DUNGEON_NPC_LIST_REGION):
                return False

        return self.dungeon_wait_healer_dialog()

    def dungeon_wait_healer_dialog(self, timeout: int = None) -> bool:
        """等万事通对话框；长时间没出现且目标就是万事通时点攻击键交互"""
        timeout = timeout if timeout is not None else self.DUNGEON_HEALER_DIALOG_TIMEOUT
        timer = Timer(timeout).start()
        interact_timer = Timer(self.DUNGEON_HEALER_INTERACT_AFTER).start()
        interactions = 0
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_DUNGEON_HEALER_DIALOG):
                logger.info('Healer dialog appeared')
                return True
            if interactions < self.DUNGEON_HEALER_MAX_INTERACT and interact_timer.reached():
                target = self.ui_target_name()
                if not target:
                    # 忘川的万事通不会自动弹对话框；到点后先尝试锁定目标
                    logger.info('No target locked, click target button')
                    self.ui_close_menu()
                    self.ui_lock_target()
                    target = self.ui_target_name()
                logger.attr('Healer target', target or 'none')
                if target and self.ocr_name_match(target, self.DUNGEON_HEALER_NAME):
                    logger.info('Target is healer, click attack/interact')
                    self.battle_click_attack()
                    interactions += 1
                    interact_timer.reset()
                else:
                    interact_timer.reset()
            self.device.sleep(1)
        logger.warning('Healer dialog does not appear')
        return False

    def dungeon_click_heal_option(self, timeout: float = 5) -> bool:
        """在万事通对话框里 OCR 定位并点击「治疗伤势」"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            roi = self.O_DUNGEON_HEAL_DIALOG.roi
            results = self.O_DUNGEON_HEAL_DIALOG.detect_and_ocr(
                self.device.image, logDisplay=False)
            for item in results:
                text = item.ocr_text.strip()
                if '治疗' not in text and '伤势' not in text:
                    continue
                xs = [point[0] for point in item.box]
                ys = [point[1] for point in item.box]
                x = int((min(xs) + max(xs)) / 2 + roi[0])
                y = int((min(ys) + max(ys)) / 2 + roi[1])
                logger.info(f'Click heal option [{text}] at ({x},{y})')
                self.device.click(x=x, y=y, control_name='dungeon_heal_option')
                self.device.click_record_clear()
                return True
            self.device.sleep(0.3)
        logger.warning('Heal option not found in dialog')
        return False

    def dungeon_wait_heal_result(self, timeout: int = None) -> bool:
        """等治疗成功/失败提示并关闭：成功 True，失败 False"""
        timeout = timeout if timeout is not None else self.DUNGEON_HEAL_RESULT_TIMEOUT
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_DUNGEON_HEAL_SUCCESS):
                logger.info('Heal success')
                self.dungeon_close_heal_prompt(self.I_DUNGEON_HEAL_SUCCESS,
                                               self.C_DUNGEON_HEAL_SUCCESS_CLOSE)
                return True
            if self.appear(self.I_DUNGEON_HEAL_FAILURE):
                logger.warning('Heal failed: healed too frequently')
                self.dungeon_close_heal_prompt(self.I_DUNGEON_HEAL_FAILURE,
                                               self.C_DUNGEON_HEAL_FAILURE_CLOSE)
                return False
            self.device.sleep(0.5)
        logger.warning('Heal result prompt does not appear')
        return False

    def dungeon_close_heal_prompt(self, prompt_rule, close_rule: RuleClick) -> None:
        """点提示框右上角 X 关闭（成功/失败位置不同）"""
        for _ in range(5):
            self.screenshot()
            if not self.appear(prompt_rule):
                return
            x, y = close_rule.coord()
            self.device.click(x=x, y=y, control_name=close_rule.name)
            self.device.click_record_clear()
            self.device.sleep(0.4)

    # ---------------------------------------------------------------- 副本内通用
    DUNGEON_WALK_TOLERANCE = 5
    DUNGEON_WALK_TIMEOUT = 90
    # 忘川/琉璃等大地图，小地图视口需要拖更多次才能到角落（默认 4 次不够）
    DUNGEON_PAN_MAX_DRAGS = 10
    # 指定目标连续几次没锁到就判定被击杀（用户规则）
    DUNGEON_FIGHT_LOCK_SWITCH = 5
    DUNGEON_FIGHT_TIMEOUT = 180

    def dungeon_wait_location(self, name: str, position: tuple = None,
                              tolerance: int = None, timeout: int = None,
                              abort_on_leave: bool = False) -> bool:
        """等人物到达指定地图/坐标；abort_on_leave=True 时离开副本地图视为死亡未通关"""
        tolerance = tolerance if tolerance is not None else self.DUNGEON_WALK_TOLERANCE
        timeout = timeout if timeout is not None else self.DUNGEON_WALK_TIMEOUT
        timer = Timer(timeout).start()
        location = None
        while not timer.reached():
            self.device.stuck_record_clear()
            location = self.map_current_location()
            if location:
                if abort_on_leave and not self.map_name_match(location[0], name):
                    logger.warning(f'Left dungeon map [{name}], now {location}')
                    self.dungeon_notify_uncleared()
                if self.map_name_match(location[0], name):
                    if position is None:
                        logger.info(f'Arrived at [{name}] {location[1]},{location[2]}')
                        return True
                    if (abs(location[1] - position[0]) <= tolerance
                            and abs(location[2] - position[1]) <= tolerance):
                        logger.info(f'Arrived at [{name}] {location[1]},{location[2]}')
                        return True
            self.device.sleep(1)
        logger.warning(f'Wait location [{name}] {position} timeout, last {location}')
        return False

    def dungeon_minimap_walk(self, corner: str, click_rule, target_map: str,
                             position: tuple = None, tolerance: int = None,
                             timeout: int = None, abort_on_leave: bool = False,
                             retries: int = 1) -> bool:
        """
        在小地图上点目标位置，人物自动寻路过去，再确认到达。

        :param corner: 小地图视口要移到的角落（top_left/bottom_left/...）
        :param click_rule: 目标点位的点击规则
        :param target_map: 目标地点名（用于确认到达）
        :param position: 目标坐标；None = 只校验地点名
        :param abort_on_leave: 副本地图内移动时，离开该地图视为死亡未通关
        :param retries: 失败后重试次数（重新开小地图、重新点击）
        """
        logger.hr(f'[{self.name}] 前往 {target_map} {position or ""}')
        tolerance = tolerance if tolerance is not None else self.DUNGEON_WALK_TOLERANCE
        location = self.map_current_location()
        if (location and self.map_name_match(location[0], target_map)
                and position is not None
                and abs(location[1] - position[0]) <= tolerance
                and abs(location[2] - position[1]) <= tolerance):
            logger.info('Already at target position')
            return True
        if abort_on_leave:
            self.dungeon_check_in_map(target_map)

        for attempt in range(retries + 1):
            if not self.map_open_minimap():
                return False
            try:
                if not self.map_pan_to_corner(corner,
                                               max_drags=self.DUNGEON_PAN_MAX_DRAGS):
                    # 大地图的四边箭头不一定可靠，视口已正确时继续点击
                    logger.warning(f'Minimap pan to {corner} failed, click target anyway')
                logger.info(f'Click minimap target {click_rule.roi_front}')
                self.click(click_rule)
                self.device.click_record_clear()
                self.device.sleep(0.5)
            finally:
                self.map_close_minimap()
            if self.dungeon_wait_location(target_map, position, tolerance, timeout,
                                          abort_on_leave=abort_on_leave):
                return True
            if attempt < retries:
                logger.warning(f'Walk to [{target_map}] failed, retry {attempt + 1}/{retries}')
                self.device.sleep(0.5)
        return False

    def dungeon_finish_detected(self, image=None) -> bool:
        """是否出现「你将在1分钟以后被传出副本」完成弹窗"""
        if image is None:
            self.screenshot()
            image = self.device.image
        return self.I_DUNGEON_FINISH_POPUP.match(image)

    # Boss 击杀奖励弹窗（沙狱[多人]沙灵神等）；子类覆盖这两个规则
    def dungeon_boss_reward_rule(self):
        return None

    def dungeon_boss_reward_close_rule(self):
        return None

    def dungeon_boss_reward_visible(self, image=None) -> bool:
        rule = self.dungeon_boss_reward_rule()
        if rule is None:
            return False
        if image is None:
            self.screenshot()
            image = self.device.image
        return rule.match(image)

    def dungeon_close_boss_reward(self) -> bool:
        """关闭 Boss 击杀奖励弹窗（默认复用 WorldBoss 的「确定」按钮）"""
        close_rule = self.dungeon_boss_reward_close_rule()
        if close_rule is None:
            return True
        for _ in range(5):
            self.screenshot()
            if not self.dungeon_boss_reward_visible():
                return True
            x, y = close_rule.coord()
            self.device.click(x=x, y=y, control_name=close_rule.name)
            self.device.click_record_clear()
            self.device.sleep(0.4)
        return not self.dungeon_boss_reward_visible()

    def dungeon_close_finish(self) -> bool:
        """关闭完成弹窗（固定位置绿 X）"""
        for _ in range(5):
            self.screenshot()
            if not self.appear(self.I_DUNGEON_FINISH_POPUP):
                return True
            x, y = self.C_DUNGEON_FINISH_CLOSE.coord()
            self.device.click(x=x, y=y, control_name=self.C_DUNGEON_FINISH_CLOSE.name)
            self.device.click_record_clear()
            self.device.sleep(0.4)
        return not self.appear(self.I_DUNGEON_FINISH_POPUP)

    def _dungeon_battle_stop(self) -> bool:
        """战斗停止条件：完成弹窗 / 目标丢失 / 目标换了 / 超时"""
        if time.time() >= self._dungeon_fight_deadline:
            logger.info('Fight deadline reached')
            return True
        image = self.device.image if self.exist_image() else None
        if image is None:
            return False
        if self.I_DUNGEON_FINISH_POPUP.match(image):
            logger.info('Dungeon finish popup appeared while fighting')
            return True
        if self.dungeon_boss_reward_visible(image=image):
            logger.info('Boss reward popup appeared while fighting')
            return True
        if not self.ui_target_locked(image=image):
            logger.info('Target lost, stop attacking')
            return True
        if self.ui_target_action(image=image) != ACTION_ATTACK:
            logger.info('Target action changed, stop attacking')
            return True
        name = self.ui_target_name()
        if name and not self.ocr_name_match(name, self._dungeon_fight_target):
            logger.info(f'Target changed to [{name}], stop attacking')
            return True
        return False

    def dungeon_fight_boss(self, target_name: str, timeout: int = None) -> str:
        """
        攻击指定 Boss 直到它被击杀或完成弹窗出现。

        规则：指定目标连续 5 次没锁到，判定目标已被击杀。

        :return: 'killed' / 'finished' / 'timeout'
        """
        logger.hr(f'[{self.name}] 战斗目标 {target_name}')
        timeout = timeout if timeout is not None else self.DUNGEON_FIGHT_TIMEOUT
        deadline = time.time() + timeout
        self._dungeon_fight_target = target_name
        self._dungeon_fight_deadline = deadline
        include_attack = self.battle_use_normal_attack()
        while True:
            if self.dungeon_finish_detected():
                logger.info('Dungeon finish popup appeared, stop fighting')
                return 'finished'
            if self.dungeon_boss_reward_visible():
                logger.info(f'Boss reward popup appeared, [{target_name}] killed')
                self.dungeon_close_boss_reward()
                return 'killed'
            if time.time() >= deadline:
                logger.warning(f'Fight timeout for [{target_name}]')
                return 'timeout'
            if not self.ui_ensure_target_locked(
                    target_name=target_name, action=ACTION_ATTACK,
                    max_switch=self.DUNGEON_FIGHT_LOCK_SWITCH, timeout=15):
                logger.info(f'[{target_name}] not locked '
                            f'{self.DUNGEON_FIGHT_LOCK_SWITCH} times, assume killed')
                return 'killed'
            self.battle_attack_loop(stop_check=self._dungeon_battle_stop,
                                    include_attack=include_attack)

    def dungeon_read_rows(self) -> list:
        """OCR 当前屏幕的左侧列表，返回 [(副本名, 剩余次数, 行中心 y), ...]"""
        self.screenshot()
        roi = self.O_DUNGEON_LIST.roi
        items = []
        for item in self.O_DUNGEON_LIST.detect_and_ocr(self.device.image, logDisplay=False):
            if not item.ocr_text or not item.ocr_text.strip():
                continue
            ys = [point[1] for point in item.box]
            items.append((item.ocr_text, (min(ys) + max(ys)) / 2 + roi[1]))
        return parse_dungeon_rows(items, with_pos=True)

    def dungeon_read_list(self) -> list:
        """OCR 当前屏幕的左侧列表，返回 [(副本名, 剩余次数), ...]"""
        return [(name, count) for name, count, _ in self.dungeon_read_rows()]

    def dungeon_scan_counts(self, max_screens: int = None,
                           stop_name: str = None) -> dict:
        """
        识别副本页所有副本的剩余次数（列表可滚动，只 OCR 不点击）

        用“分段慢滑 + 相邻屏重叠”而不是一次快速滑到底：真机实测整段快滑
        （S_DUNGEON_LIST_SWIPE）一滑就跨了约 5 行，会跳过中间的副本（例如帝玉岭[多人]）。

        :param stop_name: 找到这个副本就提前结束（只读当前副本时用）
        :return: {副本名: 剩余次数}，包含列表里的全部副本
        """
        logger.hr(f'[{self.name}] 识别副本次数')
        if not self.dungeon_open_page():
            raise GameStuckError('Dungeon page does not appear')

        max_screens = max_screens if max_screens is not None else self.DUNGEON_SCAN_MAX_SCREENS
        counts = {}
        last_names = None
        for index in range(max_screens):
            rows = self.dungeon_read_list()
            logger.info(f'Dungeon list screen {index}: {rows}')
            for name, count in rows:
                if name in counts and counts[name] != count:
                    logger.warning(f'Dungeon [{name}] count changed '
                                   f'{counts[name]} -> {count}, keep first')
                    continue
                counts.setdefault(name, count)

            if stop_name and any(dungeon_name_matches(row_name, stop_name)
                                 for row_name, _ in rows):
                logger.info(f'Dungeon [{stop_name}] found, stop scanning')
                break

            names = tuple(name for name, _ in rows)
            if rows and names == last_names:
                logger.info('Dungeon list does not scroll any more')
                break
            last_names = names
            if index >= max_screens - 1:
                break

            self.ui_swipe_gentle(self.DUNGEON_SCROLL_START, self.DUNGEON_SCROLL_END,
                                 steps=3, step_delay=0.25)
            self.device.sleep(self.DUNGEON_SCROLL_SETTLE)

        logger.attr('Dungeon counts', counts)
        return counts

    def dungeon_config_counts(self) -> dict:
        """识别次数并换算成配置字段：{字段前缀: 剩余次数}（只含已接入配置的副本）"""
        return config_counts(self.dungeon_scan_counts())

    def dungeon_field_status(self, field: str) -> tuple:
        """
        只识别当前副本：打开副本页后逐屏 OCR，找到该行就提前结束。

        全程不点击左侧列表——列表行本身带剩余次数，不需要选中副本；
        选中只在重置时使用。条目所在屏不确定，所以找不到才往下滑。
        :return: (剩余次数 | None, 特权剩余次数 | None)
        """
        name = FIELD_NAMES.get(field, field)
        counts = self.dungeon_scan_counts(stop_name=name)
        count = counts.get(name)
        privilege = self.dungeon_privilege_count()
        if count is None:
            logger.warning(f'Unable to read remaining count for [{name}], counts={counts}')
        return count, privilege

    def dungeon_privilege_count(self) -> int | None:
        """识别底部「特权额外挑战剩余次数」"""
        self.screenshot()
        results = self.O_DUNGEON_PRIVILEGE_COUNT.detect_and_ocr(
            self.device.image, logDisplay=False)
        text = ''.join(item.ocr_text for item in results)
        count = parse_privilege_count(text)
        logger.attr('Dungeon privilege count', f'{text} -> {count}')
        return count

    def dungeon_status(self) -> dict:
        """
        读取当前副本状态快照

        :return: {'counts': {配置字段: 剩余正常次数}, 'privilege': 特权剩余次数}
        """
        counts = self.dungeon_config_counts()
        privilege = self.dungeon_privilege_count()
        return {'counts': counts, 'privilege': privilege}

    def privilege_dungeon_field(self) -> str | None:
        """
        取配置里的特权副本字段（只接受已勾选且次数=2 的副本）

        配置下拉框已经按这两个条件过滤；这里再校验一次，防止旧配置/手改 json 不合法。
        """
        cfg = self.config.dungeon.dungeon_config
        field = getattr(cfg.privilege_dungeon, 'value', cfg.privilege_dungeon)
        if not field or field == 'none':
            return None
        if not getattr(cfg, f'{field}_enable', False):
            logger.warning(f'Privilege dungeon [{field}] is not enabled, ignore')
            return None
        if getattr(cfg, f'{field}_count', 0) != 2:
            logger.warning(f'Privilege dungeon [{field}] count is not 2, ignore')
            return None
        return field

    def dungeon_click_reset(self) -> bool:
        """
        点击右侧固定位置的「重置」按钮。

        重置的作用：打 2 次及以上的副本跑完一次后，列表次数可能还没减 1，
        不重置怪物也不会刷新；点击后次数才会减 1 并刷新怪物。
        调用方负责重新识别次数确认是否生效。
        """
        self.screenshot()
        if not self.dungeon_detail_ready():
            logger.warning('Dungeon detail is not ready, cannot click reset')
            return False
        logger.hr(f'[{self.name}] 重置当前副本')
        self.appear_then_click(self.I_DUNGEON_RESET_BUTTON)
        self.device.click_record_clear()
        self.device.sleep(0.5)
        return True

    # ---------------------------------------------------------------- 选中与切换
    def dungeon_feature_rules(self) -> list:
        """右侧选中特征：[(RuleImage, 副本名), ...]"""
        return [
            (self.I_SHAYU_MULTI_FEATURE, '沙狱[多人]'),
            (self.I_SHAYU_SOLO_FEATURE, '沙狱[单人]'),
            (self.I_DIYULING_MULTI_FEATURE, '帝玉岭[多人]'),
            (self.I_DIYULING_SOLO_FEATURE, '帝玉岭[单人]'),
            (self.I_LIULI_FEATURE, '琉璃幻境'),
            (self.I_XIANJU_FEATURE, '仙居福地'),
            (self.I_TIANYUAN_FEATURE, '天渊灵岛'),
            (self.I_ZHIZUN_FEATURE, '至尊巅峰'),
        ]

    def dungeon_feature_scores(self, image=None) -> list:
        """右侧特征匹配分数，按高到低返回 [(score, 副本名), ...]"""
        if image is None:
            self.screenshot()
            image = self.device.image
        scores = []
        for rule, name in self.dungeon_feature_rules():
            if rule.image is None or rule.image.size == 0:
                continue
            source = rule.corp(image)
            if source is None or source.size == 0:
                continue
            if (rule.image.shape[0] > source.shape[0]
                    or rule.image.shape[1] > source.shape[1]):
                continue
            score = float(cv2.matchTemplate(source, rule.image,
                                            cv2.TM_CCOEFF_NORMED).max())
            scores.append((score, name))
        scores.sort(reverse=True)
        return scores

    def dungeon_selected_name(self, image=None) -> str | None:
        """当前右侧选中的副本名（特征匹配），无法确定时返回 None"""
        scores = self.dungeon_feature_scores(image)
        if not scores:
            return None
        best_score, best_name = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else 0.0
        logger.attr('Dungeon selected feature',
                    f'{best_name} {best_score:.3f} second {second_score:.3f}')
        if (best_score < self.DUNGEON_FEATURE_MIN_SCORE
                or best_score - second_score < self.DUNGEON_FEATURE_MIN_MARGIN):
            return None
        return best_name

    def dungeon_detail_ready(self, image=None) -> bool:
        """
        右侧详情是否已刷新：重置按钮位置固定，每一行选中后都会出现，
        用它当“详情已加载”的标志。
        """
        if image is None:
            self.screenshot()
            image = self.device.image
        return self.I_DUNGEON_RESET_BUTTON.match(image)

    def dungeon_selected_row_center(self, image=None) -> int | None:
        """左侧列表当前选中行的中心 y（黄色描边）；识别不到返回 None"""
        if image is None:
            self.screenshot()
            image = self.device.image
        x, y, w, h = self.O_DUNGEON_LIST.roi
        roi = image[y:y + h, x:x + w].astype(int)
        red, green, blue = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
        mask = ((red > 120) & (green > 90) & (blue < 90)
                & ((red - blue) > 60))
        ys = np.nonzero(mask)[0]
        # 选中框是两条长横线；按行统计，排除小图标里的零散黄色
        per_row = np.bincount(ys, minlength=h)
        border_rows = np.nonzero(per_row >= self.DUNGEON_SELECTED_MIN_ROW_PIXELS)[0]
        if len(border_rows) < 2:
            return None
        return int((border_rows.min() + border_rows.max()) / 2 + y)

    def dungeon_row_selected(self, name: str, fallback_y: int) -> bool:
        """点击行后确认选中：黄色选中框对齐目标行；识别不到再回退到右侧特征"""
        image = self.screenshot()
        selected_y = self.dungeon_selected_row_center(image)
        if selected_y is not None:
            rows = self.dungeon_read_rows()
            target_y = next((row_y for row_name, _, row_y in rows
                             if dungeon_name_matches(row_name, name)), None)
            if target_y is not None:
                logger.attr('Dungeon selected row', f'{selected_y} vs {target_y}')
                return abs(selected_y - target_y) <= self.DUNGEON_SELECTED_ROW_TOLERANCE
            if abs(selected_y - fallback_y) <= self.DUNGEON_SELECTED_ROW_TOLERANCE:
                return True
        selected = self.dungeon_selected_name(image)
        if selected and dungeon_name_matches(selected, name):
            logger.info(f'Dungeon [{name}] selected (feature verified)')
            return True
        return False

    def dungeon_select(self, name: str, max_scrolls: int = None) -> bool:
        """
        点击左侧列表选中指定副本（只在重置前调用）。

        按 OCR 找到目标行的实际 y 再点击（列表滚动/回弹后行位置会变，不能用
        固定坐标）；点击后优先用左侧黄色选中框确认，其次才用右侧特征兜底。
        找不到目标行或确认不了就返回 False，不再逐行乱点。
        """
        logger.hr(f'[{self.name}] 选中副本 -> {name}')
        if not self.dungeon_open_page():
            raise GameStuckError('Dungeon page does not appear')

        max_scrolls = max_scrolls if max_scrolls is not None else self.DUNGEON_SCAN_MAX_SCREENS
        for index in range(max_scrolls):
            rows = self.dungeon_read_rows()
            logger.info(f'Dungeon rows: {rows}')
            target = next(((row_name, row_y) for row_name, _, row_y in rows
                           if dungeon_name_matches(row_name, name)), None)
            if target is not None:
                for attempt in range(2):
                    logger.info(f'Click dungeon row [{target[0]}] at '
                                f'({self.DUNGEON_ROW_X}, {target[1]})')
                    self.device.click(x=self.DUNGEON_ROW_X, y=target[1],
                                      control_name='dungeon_row')
                    self.device.click_record_clear()
                    self.device.sleep(self.DUNGEON_CLICK_SETTLE)
                    if self.dungeon_row_selected(name, target[1]):
                        logger.info(f'Dungeon [{name}] selected')
                        return True
                    logger.warning(f'Dungeon [{name}] row click not verified, '
                                   f'retry {attempt + 1}/2')
                    rows = self.dungeon_read_rows()
                    target = next(((row_name, row_y) for row_name, _, row_y in rows
                                   if dungeon_name_matches(row_name, name)), None)
                    if target is None:
                        break
                return False

            if index >= max_scrolls - 1:
                break
            self.ui_swipe_gentle(self.DUNGEON_SCROLL_START, self.DUNGEON_SCROLL_END,
                                 steps=3, step_delay=0.25)
            self.device.sleep(self.DUNGEON_SCROLL_SETTLE)

        logger.warning(f'Dungeon [{name}] not found in list')
        return False

    # ---------------------------------------------------------------- 通用能力
    def prepare(self, timeout: int = 60) -> None:
        """确保游戏在线并停留在主页面（不在主页面先回，回不去则重启游戏）"""
        logger.hr(f'[{self.name}] 准备主页面')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            return
        self.ui_reset_current_page()
        try:
            if self.ui_goto(page_main, timeout=timeout):
                # 关掉会盖住入口的主页面弹窗（活动弹窗等）
                self.map_close_main_popup()
                return
            logger.warning('无法切回主页面，重启游戏')
        except (GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'当前页面异常（{e}），重启游戏')
        self.ui_restart_game()
