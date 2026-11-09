# This Python file uses the following encoding: utf-8
"""
伏魔山涧（FumoShanjian）

当前进度：
  第 0 步：按任务方式决定要不要做。
    「只完成活跃任务」（默认）：先查活动-活跃页里的「伏魔山涧」是否已完成，
    已完成就直接收工；「一直继续直到正常结束」不查活跃度。
  第 1 步：通过万剑冢的伏魔大使 NPC 打开对话。
    准备主页面 -> 确保在万剑冢 -> 打开小地图 -> 视口拖到右上角
    -> 点击小地图上的伏魔大使标记 -> 关闭小地图 -> 轮询「伏魔大使」对话模板。
  第 2 步：OCR 对话选项区找到「进入伏魔涧」的文字位置并点击
    -> 点完直接进入副本地图 -> 主动关闭只显示 3 秒的进图提示弹窗
    -> 等右上角地点从万剑冢切走。
  第 3 步：副本内打怪。
    小地图视口拖到左上角，按顺序点打怪点（501,336 / 463,399 / 574,320 循环），
    点完关小地图，人物自动寻路，途中锁可攻击目标，锁到就按战斗预设打。
    移动途中锁不到目标不计数；连续两次读到相同坐标（已到达点击位置）后仍锁不到，
    才记一次失败，连续 2 次失败换下一个打怪点。
  第 4 步：结束条件。
    - 正常结束 / 死亡低伤害提前退出：人物自动离开副本，出现「伏魔涧奖励结算」弹窗，
      关掉即可；只完成活跃任务模式下会再次进副本，进度保留，直到进度满；
    - 只完成活跃任务：读底部进度 OCR，满 250000/250000 或显示「已达上限」后重启游戏退出副本，
      上线后复查活动-活跃页，完成则关闭活跃页面回主页面结束。
  死亡复活：出现死亡弹窗时复活，然后按当前步骤继续执行，不重跑整个任务。
  掉线处理：出现「您掉线了...」时直接重启游戏，上线后同样按当前步骤继续。
"""
import re

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd,
                              TaskFailed)
from module.logger import logger
from tasks.FumoShanjian.assets import FumoShanjianAssets
from tasks.FumoShanjian.config import FumoTaskMode
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main


class DeathResume(Exception):
    """角色死亡并已复活：当前步骤需要重新执行"""
    pass


class ScriptTask(GameUi, FumoShanjianAssets):
    name = '伏魔山涧'
    SCHEDULER_NAME = 'FumoShanjian'

    # NPC 所在地图
    LOCATION = '万剑冢'
    # 副本内地图名（右上角地点文字）
    DUNGEON_LOCATION = '伏魔涧'
    # 活动-活跃页里对应的任务名
    ACTIVE_TASK_NAME = '伏魔山涧'
    # 活跃任务进度目标：副本底部 OCR 读到 250000/250000 或「已达上限」即活跃任务完成
    ACTIVE_PROGRESS_TARGET = 250000
    # 小地图打怪点：按顺序循环点击，连续 TARGET_MISS_LIMIT 次锁不到可攻击目标就换下一个
    MINIMAP_POINTS = (
        'C_FUMO_MINIMAP_POINT_1',
        'C_FUMO_MINIMAP_POINT_2',
        'C_FUMO_MINIMAP_POINT_3',
    )
    # 连续 2 次锁不到可攻击目标就换小地图点
    TARGET_MISS_LIMIT = 2
    # 单次尝试锁目标：最多点 2 次「目标」按钮、2 秒超时（避免在一个点卡太久）
    FUMO_LOCK_MAX_SWITCH = 2
    FUMO_LOCK_TIMEOUT = 2

    def run(self) -> None:
        logger.hr('伏魔山涧')
        active_only = (self.config.fumo_shanjian.fumo_shanjian_config.task_mode
                       == FumoTaskMode.ACTIVE_ONLY)
        try:
            self.run_step('准备游戏', self.prepare_game)
            if self.run_step('检查活跃任务', self.active_task_finished):
                logger.info(f'活跃任务[{self.ACTIVE_TASK_NAME}]已完成，今天不再进副本')
                raise TaskEnd('FumoShanjian')

            # 只完成活跃任务时，死亡/低伤害会提前退出副本：
            # 关掉结算弹窗后再进一次，进度会保留，直到进度满才收工
            while True:
                if not self.run_step('进入副本', self.enter_dungeon_flow):
                    raise TaskFailed('进入伏魔涧失败')
                logger.info('已进入伏魔涧，开始副本内轮询')
                result = self.run_step('副本内打怪', self.handle_dungeon)
                if result == 'active_done' or not active_only:
                    break
                logger.info('副本已结束但活跃任务进度未满，重新进入副本继续')
                self.run_step('准备游戏', self.prepare_game)
        finally:
            # 正常/异常都重排，避免 next_run 停在过去霸占调度
            self.set_next_run(task='FumoShanjian', success=True, finish=True)
        raise TaskEnd('FumoShanjian')

    def run_step(self, name: str, func):
        """
        执行一个任务步骤；步骤内角色死亡并复活后，重新执行当前步骤

        死亡由 `handle_death` 复活后抛 `DeathResume`，这里接住并重试同一个步骤，
        不会把整个任务从头重跑。重试前清掉页面缓存，让导航重新识别当前页面。
        """
        self._current_step = name
        while True:
            try:
                return func()
            except DeathResume:
                logger.warning(f'角色死亡并复活，继续当前步骤：{name}')
                self.ui_reset_current_page()
                self.device.sleep(1)

    def enter_dungeon_flow(self) -> bool:
        """完整进入副本流程：打开伏魔大使对话 -> 点「进入伏魔涧」"""
        if not self.open_npc_dialog():
            return False
        logger.info('伏魔大使对话已打开')
        return self.enter_dungeon()

    # ---------------------------------------------------------------- 准备
    def prepare_game(self) -> None:
        """确保游戏在线并停留在主页面"""
        logger.hr('准备游戏（在线 + 主页面）')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            logger.info('游戏已登录到主页面')
            return
        try:
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=60):
                raise GameStuckError('Main page does not appear')
            logger.info('游戏在线且在主页面')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError):
            logger.warning('无法切回主页面，重启游戏')
            self.ui_restart_game()

    def active_task_finished(self) -> bool:
        """
        任务方式为「只完成活跃任务」时，查活动-活跃页里的「伏魔山涧」是否已完成

        - 「一直继续直到正常结束」：不查活跃度，直接返回 False（继续进副本）
        - 查询过程异常（页面进不去 / 游戏没运行）：按未完成处理，继续进副本，
          宁可多打一次也不要把当天任务误跳过
        - 查完未完成：先回主页面，后续小地图导航必须从主页面开始

        :return: True 表示活跃任务已完成，本次直接收工
        """
        cfg = self.config.fumo_shanjian.fumo_shanjian_config
        if cfg.task_mode != FumoTaskMode.ACTIVE_ONLY:
            logger.info('任务方式：一直继续直到正常结束，跳过活跃度检查')
            return False

        logger.hr('检查活跃任务')
        try:
            completed = self.active_task_completed(self.ACTIVE_TASK_NAME)
        except (GameStuckError, GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'无法确认活跃任务状态（{e}），按未完成处理')
            completed = False
        logger.attr('活跃任务完成', completed)
        if completed:
            return True

        # 活跃度检查打开了活动弹窗，回主页面再继续
        self.back_to_main_from_activity()
        return False

    def back_to_main_from_activity(self) -> None:
        """关闭活动弹窗回主页面（活跃度检查 / 复查之后用）"""
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            logger.warning('无法从活动页回到主页面，重启游戏')
            self.ui_restart_game()

    def clear_blocking_popups(self, max_close: int = 3) -> None:
        """
        关掉挡住小地图入口的残留弹窗（如上次留下的 NPC 对话框、活动弹窗）

        模态弹窗会吞掉小地图入口的点击，不关掉会连点无效并触发连点保护。
        """
        logger.hr('Clear blocking popups')
        for _ in range(max_close):
            self.reset_records()
            if not self.try_close_unknown_page(skip_screenshot=False):
                break
            self.device.sleep(0.5)
        self.map_close_main_popup()

    # ---------------------------------------------------------------- NPC 对话
    def ensure_location(self) -> None:
        """确保角色在万剑冢（已经在就不传送，世界地图列表不含当前地点）"""
        location = self.map_move_read_pos()
        if location is not None and self.map_name_match(location[0], self.LOCATION):
            logger.attr('Location', f'Already at [{location[0]}] {location[1]},{location[2]}, skip teleport')
            return
        if not self.map_teleport(self.LOCATION):
            raise GameStuckError(f'Cannot teleport to [{self.LOCATION}]')

    def open_npc_dialog(self) -> bool:
        """
        打开伏魔大使对话：小地图视口拖到右上角 -> 点 NPC 标记 -> 关小地图 -> 轮询对话模板
        """
        self.clear_blocking_popups()
        self.ensure_location()

        logger.hr('Open Fumo NPC dialog')
        self.map_close_minimap()
        self.device.sleep(0.3)
        if not self.map_open_minimap():
            raise GameStuckError('Minimap does not appear')
        if not self.map_pan_to_corner('top_right'):
            # 视口没到角落时点击坐标可能偏，先记下来；对话没出现时上层会报失败
            logger.warning('Minimap does not reach top_right, continue with the configured click point')
        self.click(self.C_FUMO_NPC)
        self.device.click_record_clear()
        self.map_close_minimap()

        cfg = self.config.fumo_shanjian.fumo_shanjian_config
        return self.wait_npc_dialog(timeout=int(cfg.npc_wait_timeout))

    def wait_npc_dialog(self, timeout: int = 20) -> bool:
        """轮询「伏魔大使」对话模板，出现返回 True"""
        logger.hr('Wait Fumo NPC dialog')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_FUMO_DIALOG):
                logger.info('Fumo NPC dialog appeared')
                return True
            self.device.sleep(0.3)
        logger.warning('Fumo NPC dialog does not appear')
        return False

    # ---------------------------------------------------------------- 进入副本
    def read_dialog_options(self) -> list[tuple[str, int, int]]:
        """
        OCR 伏魔大使对话框的选项区

        返回 [(文字, 屏幕x, 屏幕y)]，坐标是文字框中心，可直接点击。
        detect_and_ocr 返回的 box 是相对 ROI 的，要加上 ROI 左上角才是屏幕坐标。
        """
        self.screenshot()
        roi = self.O_FUMO_DIALOG_OPTIONS.roi
        options = []
        for item in self.O_FUMO_DIALOG_OPTIONS.detect_and_ocr(self.device.image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text:
                continue
            xs = [point[0] for point in item.box]
            ys = [point[1] for point in item.box]
            x = int((min(xs) + max(xs)) / 2) + roi[0]
            y = int((min(ys) + max(ys)) / 2) + roi[1]
            options.append((text, x, y))
        return options

    def find_dialog_option(self, option: str, retry: int = 3) -> tuple[int, int] | None:
        """
        在对话选项里找 option 的文字位置

        用 ocr_name_match 容忍 OCR 形近字（如「涧」被认成「润」）；
        找不到返回 None。
        """
        for attempt in range(1, retry + 1):
            self.reset_records()
            options = self.read_dialog_options()
            logger.info(f'Dialog options: {[text for text, _, _ in options]}')
            for text, x, y in options:
                if not self.ocr_name_match(text, option):
                    continue
                logger.info(f'Dialog option [{text}] found at ({x},{y})')
                return x, y
            logger.warning(f'Dialog option [{option}] not found (attempt {attempt}/{retry})')
            self.device.sleep(0.4)
        return None

    def enter_dungeon(self) -> bool:
        """
        找到并点击「进入伏魔涧」，随后等地点切换到副本

        点选项会直接进入副本地图，不需要再点「确定」。
        """
        cfg = self.config.fumo_shanjian.fumo_shanjian_config
        logger.hr(f'Enter dungeon [{cfg.enter_option}]')
        coord = self.find_dialog_option(cfg.enter_option)
        if coord is None:
            logger.warning(f'Enter option [{cfg.enter_option}] does not appear')
            return False
        logger.info(f'Click enter option [{cfg.enter_option}] at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='fumo_dialog_option')
        self.device.click_record_clear()
        self.device.sleep(0.5)
        return self.wait_enter_dungeon(int(cfg.enter_wait))

    def close_enter_popup(self) -> bool:
        """
        关闭进图后的提示弹窗（「击败妖魔，战个痛快！」）

        弹窗只显示 3 秒，看到就点右上角绿 X；没看到或点不到返回 False。
        """
        if not self.appear(self.I_FUMO_ENTER_POPUP):
            return False
        if not self.appear_then_click(self.I_FUMO_ENTER_POPUP_CLOSE):
            logger.warning('Enter popup appeared but close button not found')
            return False
        logger.info('Enter popup closed')
        self.device.click_record_clear()
        return True

    def wait_enter_dungeon(self, timeout: int = 20) -> bool:
        """
        等进入副本：右上角地点从万剑冢切走即成功

        点击「进入伏魔涧」后直接进图，不需要再点确认按钮。
        进图后会弹一个只显示 3 秒的提示弹窗，看到就点绿 X 主动关闭。
        """
        logger.hr('Wait entering Fumo dungeon')
        timer = Timer(timeout).start()
        outside = 0
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            # 进图提示弹窗只显示 3 秒：主动点绿X关掉，别让它挡住后续操作
            self.close_enter_popup()
            location = self.map_current_location(image=self.device.image)
            if location is not None and not self.map_name_match(location[0], self.LOCATION):
                # 连续两帧读到新地点才算进图，避免地点 OCR 抖动误判
                outside += 1
                if outside >= 2:
                    logger.info(f'Entered dungeon: {location[0]} {location[1]},{location[2]}')
                    return True
            else:
                outside = 0
            self.device.sleep(0.4)
        logger.warning(f'Still at [{self.LOCATION}] after {timeout}s, enter dungeon failed')
        return False

    # ---------------------------------------------------------------- 副本内
    def handle_dungeon(self) -> str:
        """
        副本内打怪 + 结束条件：

        - 小地图拖到左上角点打怪点 -> 人物自动寻路，边移动边锁可攻击目标；
        - 锁定到可攻击目标就按「战斗配置」的预设打，打到目标丢失再继续找；
        - **移动途中锁不到目标不算失败**：只有连续两次读到相同坐标（已到达点击位置）
          且没锁到可攻击目标才算一次失败；连续 TARGET_MISS_LIMIT 次失败后换下一个点：
          501,336 -> 463,399 -> 574,320 -> 循环；
        - 正常结束 / 死亡：人物自动离开副本并弹「伏魔涧奖励结算」，
          关掉后返回 'finished'（只完成活跃任务模式下由调用方再次进副本，
          进度会保留）。副本内死亡不会弹死亡弹窗，所以不需要复活；
        - 掉线重启 / 死亡复活后不在副本：没有结算弹窗时在当前步骤内重新进副本继续；
        - 只完成活跃任务：底部进度 OCR 满 250000/250000 或显示「已达上限」表示活跃任务完成，
          直接重启游戏退出副本，上线后复查活跃任务，返回 'active_done'。

        :return: 'active_done' 活跃任务完成并已复查收工；
                 'finished' 副本正常结束（结算弹窗已关闭）
        """
        logger.hr('副本内轮询')
        active_only = (self.config.fumo_shanjian.fumo_shanjian_config.task_mode
                       == FumoTaskMode.ACTIVE_ONLY)
        logger.attr('任务方式', '只完成活跃任务' if active_only else '一直继续直到正常结束')

        # 死亡复活后可能已经被游戏送出副本：先看一眼还在不在副本里，
        # 不在就结束本轮，由外层决定重新进副本（只完成活跃任务模式）。
        self.screenshot()
        if self.appear(self.I_FUMO_FINISH_POPUP):
            logger.info('再次进入副本前发现奖励结算弹窗，先关闭')
            self.close_finish_popup()
            return 'finished'
        inside = self.in_dungeon()
        if inside is False:
            logger.warning(f'当前不在[{self.DUNGEON_LOCATION}]，重新进入副本')
            if not self.enter_dungeon_flow():
                raise TaskFailed('重新进入伏魔涧失败')
            logger.info('已重新进入伏魔涧')

        point_index = 0
        misses = 0
        last_position = None
        # 进图提示弹窗可能刚好还在，再确认一次再开小地图
        self.close_enter_popup()
        # 关掉提示弹窗后，先开小地图拖到左上角点第一个打怪点
        self.click_minimap_point(self.minimap_point(point_index))
        last_position = None

        while True:
            self.reset_records()
            self.screenshot()

            # 正常结束 / 死亡都是直接离开副本并弹奖励结算，人物已经在副本外
            if self.appear(self.I_FUMO_FINISH_POPUP):
                logger.info('副本已结束（出现奖励结算弹窗）')
                self.close_finish_popup()
                return 'finished'

            if active_only:
                progress = self.read_active_progress()
                if progress is not None and progress[0] >= self.ACTIVE_PROGRESS_TARGET:
                    logger.info(f'活跃任务进度已满：{progress[0]}/{progress[1]}，重启游戏退出副本')
                    self.exit_dungeon_by_restart()
                    return 'active_done'

            # 判断是否已到达点击位置：连续两次读到相同坐标才算到达。
            # 移动途中锁不到目标不算失败，避免刚点完还在赶路就换点。
            position = self.map_current_location(image=self.device.image)
            arrived = False
            if position is not None:
                current = (position[1], position[2])
                if last_position is not None and current == last_position:
                    arrived = True
                last_position = current
            else:
                last_position = None

            # 已经锁到可攻击目标：直接打，打到目标丢失再回来找下一个
            if self.ui_target_attackable(image=self.device.image):
                misses = 0
                if self.battle_attack() == 'finished':
                    logger.info('战斗中出现奖励结算弹窗')
                    self.close_finish_popup()
                    return 'finished'
                continue

            # 没锁到就点「目标」找一次；移动途中找不到不计数
            if self.ui_ensure_target_locked(max_switch=self.FUMO_LOCK_MAX_SWITCH,
                                            timeout=self.FUMO_LOCK_TIMEOUT,
                                            abort_check=self.battle_interrupted):
                misses = 0
                if self.battle_attack() == 'finished':
                    logger.info('战斗中出现奖励结算弹窗')
                    self.close_finish_popup()
                    return 'finished'
                continue

            # 锁目标途中弹出了结算：别再开小地图点下一个点，回外层处理
            if self.battle_interrupted():
                continue

            if not arrived:
                logger.info('还在移动中，未锁定目标不计失败')
                self.device.sleep(0.2)
                continue

            misses += 1
            logger.info(f'已到达点击位置，没锁到可攻击目标（{misses}/{self.TARGET_MISS_LIMIT}）')
            if misses >= self.TARGET_MISS_LIMIT:
                misses = 0
                point_index = (point_index + 1) % len(self.MINIMAP_POINTS)
                self.click_minimap_point(self.minimap_point(point_index))
                last_position = None
            self.device.sleep(0.2)

    def in_dungeon(self, retry: int = 3) -> bool | None:
        """
        当前是否在伏魔涧

        :return: True=在副本内 / False=已离开 / None=连续几次都读不到坐标（未知）
        """
        for _ in range(retry):
            location = self.map_current_location()
            if location is not None:
                return self.map_name_match(location[0], self.DUNGEON_LOCATION)
            self.device.sleep(0.3)
        return None

    def handle_death(self) -> None:
        """
        角色死亡：复活后抛 DeathResume，由 run_step 重新执行当前步骤

        副本内死亡由游戏自动退出并弹奖励结算，不会进到这里；
        这里处理会弹死亡弹窗的场景（副本外 / 进图途中）。不能走全局的
        「复活 + rerun_task」：那会把整个任务从头重跑，而不是继续当前步骤。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        step = getattr(self, '_current_step', '未知')
        logger.warning(f'角色死亡（当前步骤：{step}），复活后继续')
        self.revive()
        self.device.sleep(1)
        self.handle_death_resume()

    def handle_death_resume(self) -> None:
        """死亡复活 / 掉线重启后：继续当前步骤，不重跑整个任务"""
        self.ui_reset_current_page()
        raise DeathResume

    def minimap_point(self, index: int):
        """小地图打怪点规则（按索引循环）"""
        return getattr(self, self.MINIMAP_POINTS[index % len(self.MINIMAP_POINTS)])

    def click_minimap_point(self, rule) -> bool:
        """
        打开小地图 -> 视口拖到左上角 -> 点击打怪点 -> 关闭小地图

        点击后人物会朝该点自动寻路，随后由 handle_dungeon 一边移动一边锁目标。
        """
        logger.info(f'Click minimap point {rule.name}')
        if not self.map_open_minimap():
            logger.warning('Minimap does not appear')
            return False
        if not self.map_pan_to_corner('top_left'):
            logger.warning('Minimap does not reach top_left, click the point anyway')
        self.click(rule)
        self.device.click_record_clear()
        self.map_close_minimap()
        self.device.sleep(0.5)
        return True

    def battle_attack(self) -> str:
        """
        按任务「战斗配置」里的预设打当前目标，直到目标丢失 / 结算弹窗出现

        :return: battle_skill_loop 的结果；'no_slot' 表示技能位没识别出来，只补了普通攻击
        """
        preset = self.battle_preset()
        slots = self.battle_plan_slots(preset=preset)
        if not slots:
            logger.warning('没有识别到可释放技能位，先补一次普通攻击')
            self.battle_click_attack()
            self.device.sleep(0.5)
            return 'no_slot'
        return self.battle_skill_loop(
            slots,
            check_stop=self.battle_attack_stop_reason,
            target_check=lambda image: self.ui_target_attackable(image=image),
            include_attack=self.battle_use_normal_attack(),
        )

    def battle_attack_stop_reason(self, image=None) -> str | None:
        """战斗停止条件：出现奖励结算弹窗（正常结束 / 死亡退出）"""
        if image is None:
            image = self.device.image if self.exist_image() else None
        if image is not None and self.I_FUMO_FINISH_POPUP.match(image):
            logger.info('战斗停止：出现奖励结算弹窗')
            return 'finished'
        return None

    def battle_interrupted(self) -> bool:
        """
        战斗被打断（奖励结算弹窗出现）：锁目标的循环要尽快退出

        只看最新一帧、不截图（它会被锁目标循环反复调用）。
        """
        image = self.device.image if self.exist_image() else None
        if image is None:
            return False
        return bool(self.I_FUMO_FINISH_POPUP.match(image))

    def read_active_progress(self) -> tuple[int, int] | None:
        """
        读副本底部的活跃任务进度

        进度未满时是数字（如 0/250000），满值后游戏改成显示「已达上限」，
        两种都按完成处理。

        :return: (已做, 总数)；已达上限返回 (目标值, 目标值)；
                 OCR 读不到或解析失败返回 None
        """
        text = (self.O_FUMO_ACTIVE_PROGRESS.ocr(self.device.image) or '').strip()
        # 满值后不再显示数字，而是文字「已达上限」（OCR 偶尔带尾巴也不影响包含判断）
        if '已达上限' in text:
            logger.attr('活跃任务进度', '已达上限')
            return self.ACTIVE_PROGRESS_TARGET, self.ACTIVE_PROGRESS_TARGET
        match = re.search(r'(\d+)\s*[/／]\s*(\d+)', text)
        if not match:
            logger.warning(f'活跃任务进度识别失败: [{text}]')
            return None
        done, total = int(match.group(1)), int(match.group(2))
        logger.attr('活跃任务进度', f'{done}/{total}')
        return done, total

    def close_finish_popup(self, timeout: int = 5) -> bool:
        """
        关闭「伏魔涧奖励结算」弹窗

        弹窗出现时点右上角绿 X，直到标题消失；关不掉只告警，不影响任务结束。
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            if not self.appear(self.I_FUMO_FINISH_POPUP):
                logger.info('Finished popup closed')
                return True
            if self.appear_then_click(self.I_FUMO_FINISH_POPUP_CLOSE):
                logger.info('Click finished popup close')
                self.device.click_record_clear()
            self.device.sleep(0.5)
            self.screenshot()
        logger.warning('Finished popup does not close')
        return False

    def exit_dungeon_by_restart(self) -> None:
        """
        活跃任务进度已满：直接重启游戏退出副本，上线后复查活跃任务
        """
        logger.hr('重启游戏退出副本')
        self.ui_restart_game()
        self.run_step('复查活跃任务', self.verify_active_task_after_exit)

    def verify_active_task_after_exit(self) -> None:
        """
        退出副本后复查活动-活跃页里的「伏魔山涧」是否已完成

        已完成：关闭活跃页面回主页面，任务正常结束；
        未完成：通知并抛 TaskFailed，交给调度按失败处理。
        """
        logger.hr('复查活跃任务')
        try:
            completed = self.active_task_completed(self.ACTIVE_TASK_NAME)
        except (GameStuckError, GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'无法复查活跃任务状态（{e}），按未完成处理')
            completed = False
        logger.attr('活跃任务完成', completed)
        if not completed:
            logger.error(f'活跃任务[{self.ACTIVE_TASK_NAME}]未完成，请检查')
            self.config.notifier.push(title='伏魔山涧FumoShanjian',
                                      content=f'<{self.config.config_name}> 进度已满但活跃任务未完成，请检查')
            raise TaskFailed(f'活跃任务[{self.ACTIVE_TASK_NAME}]未完成')

        logger.info(f'活跃任务[{self.ACTIVE_TASK_NAME}]已完成，关闭活跃页面回主页面')
        self.back_to_main_from_activity()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).read_dialog_options()
    except TaskEnd:
        pass
