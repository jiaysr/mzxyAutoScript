# This Python file uses the following encoding: utf-8
"""
限时活动-野外雪人（WildSnowman）

流程：
    主页面 -> 传送到配置的地图（当前只支持域外灵岛）
    -> 用小地图坐标寻路模块走到配置的打怪坐标（默认 251,175）
    -> 只打「陵鱼 / 陵魚 / 可爱雪人 / 可愛雪人」
    -> 每 N 分钟（默认 40）找万事通治疗并修理装备，然后回打怪点
    -> 任务优先级最低：启用后一直打，检测到更高优先级任务到期时让路，
       立即重排自己，等正常任务跑完再继续
    -> 死亡复活后重新前往打怪坐标，并重置治疗/修理计时
    -> 掉线重启后同样重新前往打怪坐标，但不重置治疗/修理计时

沼泽暂不开放：万事通点位还没录入，治疗/修理无法完成。
"""
import math
import re
from datetime import datetime, timedelta

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd, TaskFailed
from module.logger import logger
from tasks.Battle.target_action import ACTION_ATTACK
from tasks.Dungeon.common import DungeonBase
from tasks.GameUi.pages.page import page_main
from tasks.LimitedTime.WildSnowman.assets import WildSnowmanAssets


class ScriptTask(DungeonBase, WildSnowmanAssets):
    SCHEDULER_NAME = 'WildSnowman'
    name = '野外雪人'

    # 默认打怪地图；实际使用任务配置里的 map_name
    MAP_NAME = '域外灵岛'
    # 万事通定点目前只录了域外灵岛；其它地图走通用 NPC 列表
    HEALER_MAP = '域外灵岛'
    # 万事通在小地图右上角；打怪点已改用地图坐标寻路
    MINIMAP_CORNER = 'top_right'
    # 小地图坐标寻路到打怪点的超时
    MAP_WALK_TIMEOUT = 120

    # 检查其他任务/配置/修理计时的间隔（秒）
    YIELD_CHECK_INTERVAL = 5
    CONFIG_CHECK_INTERVAL = 30
    # 让路后稍等几秒再重排，保证 FIFO 模式下其它到期任务排在前面
    YIELD_RESUME_SECONDS = 5

    # 每成功释放这么多次技能回一次基准点
    RECOVER_EVERY_CASTS = 6
    # 预设模式：普通攻击最小间隔
    NORMAL_ATTACK_INTERVAL = 1.0
    # 预设模式：技能冷却轮询间隔
    SKILL_COOLDOWN_POLL = 0.1
    # 技能位识别重试
    SLOT_RECOGNIZE_ATTEMPTS = 3
    SLOT_RECOGNIZE_WAIT = 1.0

    # 目标切换次数/超时
    TARGET_SWITCH_MAX = 8
    TARGET_SWITCH_TIMEOUT = 10
    TARGET_WARN_AFTER = 10

    def run(self) -> None:
        logger.hr('野外雪人')
        success = False
        self._yielded = False
        self._stopped_by_user = False
        self._resume_reposition = False
        self._reset_recover_after_resume = False
        self._last_recover = datetime.now()
        self._casts = 0
        self._target_fail_count = 0
        self._yield_timer = Timer(self.YIELD_CHECK_INTERVAL).start()
        self._config_timer = Timer(self.CONFIG_CHECK_INTERVAL).start()
        # ConfigWatcher：运行中检测 UI 里是否把任务关掉
        self.config.start_watching()

        try:
            if not self.ensure_in_map_and_position(reset_recover=True):
                raise GameStuckError('Cannot reach Wild Snowman battle position')
            self.battle_loop()
            success = True
        finally:
            if self._yielded or self._stopped_by_user:
                # 让路 / 被 UI 关闭：稍后重排自己。关闭期间调度器不会运行，
                # 重新启用后能立刻继续，而不是等到第二天。
                resume_at = datetime.now() + timedelta(seconds=self.YIELD_RESUME_SECONDS)
                logger.attr('恢复时间', resume_at.strftime('%H:%M:%S'))
                self.set_next_run(task='WildSnowman', target=resume_at,
                                  success=None, finish=True, server=False)
            else:
                self.set_next_run(task='WildSnowman', success=success, finish=True)
        raise TaskEnd('WildSnowman')

    # ---------------------------------------------------------------- 配置
    def battle_task_battle_config(self):
        """本任务的战斗配置组（预设 / 普攻 / 快速补锁都从这里读）"""
        return getattr(self.config.wild_snowman, 'battle_config', None)

    def battle_task_preset(self) -> str:
        """读取本任务自己的 battle_config，而不是副本任务的预设"""
        battle = self.battle_task_battle_config()
        return str(getattr(battle, 'battle_preset', '') or '')

    def current_config(self):
        return self.config.wild_snowman.wild_snowman_config

    def map_name(self) -> str:
        """配置的打怪地图名（枚举值转字符串，旧配置缺字段时回退默认地图）"""
        value = getattr(self.current_config(), 'map_name', self.MAP_NAME)
        return str(getattr(value, 'value', value) or self.MAP_NAME)

    def battle_position(self) -> tuple[int, int]:
        text = str(self.current_config().battle_coord or '251,175')
        try:
            parts = [int(item.strip()) for item in re.split(r'[,，\s]+', text) if item.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]
        except ValueError:
            pass
        logger.warning(f'Invalid battle_coord [{text}], use default (251,175)')
        return 251, 175

    def battle_tolerance(self) -> int:
        return int(self.current_config().battle_tolerance)

    def target_names(self) -> list:
        text = str(self.current_config().target_names or '')
        return [name for name in re.split(r'[,，;；\s]+', text) if name]

    # ---------------------------------------------------------------- 进入地图 / 打怪点
    def ensure_in_map_and_position(self, reset_recover: bool = False) -> bool:
        """确保在配置地图，并走到打怪坐标附近（路上死亡会自动重试）"""
        logger.hr('前往野外雪人打怪点')
        map_name = self.map_name()
        logger.attr('打怪地图', map_name)
        for attempt in range(1, 4):
            if self._resume_reposition:
                # 走位过程中死亡/掉线：清掉标记，下一轮从复活点重新传送
                reset_recover = reset_recover or self._reset_recover_after_resume
                self._resume_reposition = False
                self._reset_recover_after_resume = False
                logger.warning('走位过程中死亡/掉线，重新前往打怪点')
            if self._ensure_in_map_and_position_once(reset_recover=reset_recover):
                self._resume_reposition = False
                self._reset_recover_after_resume = False
                return True
            logger.warning(f'前往打怪点失败（{attempt}/3），重试')
            self.device.sleep(1)
        return False

    def _ensure_in_map_and_position_once(self, reset_recover: bool = False) -> bool:
        map_name = self.map_name()
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            logger.warning('Main page does not appear')
            return False
        self.map_close_main_popup()

        location = self.map_current_location()
        if location is None or not self.map_name_match(location[0], map_name):
            logger.info(f'当前不在[{map_name}]，先传送')
            if not self.map_teleport(map_name):
                logger.warning(f'Teleport to [{map_name}] failed')
                return False
            self.device.sleep(1)

        if not self.walk_to_battle_position():
            if self._resume_reposition:
                # 路上死亡/掉线：直接交给外层重试，不再在错误的地图里用摇杆
                return False
            # 小地图坐标寻路偶尔会被地形卡住，用摇杆闭环再兜底走一次
            logger.warning('Minimap coordinate walk failed, fallback to map_move_to')
            position = self.battle_position()
            if not self.map_move_to(position[0], position[1], map_name=map_name,
                                    tolerance=self.battle_tolerance(), timeout=60,
                                    ensure_main=False):
                logger.warning('Cannot reach battle position')
                return False

        if reset_recover:
            self._last_recover = datetime.now()
            logger.attr('修理计时', '已重置')
        self.ui_close_menu()
        self.device.sleep(0.3)
        return True

    def walk_to_battle_position(self) -> bool:
        """按打怪坐标用小地图坐标导航自动寻路；中途死亡会提前退出"""
        map_name = self.map_name()
        position = self.battle_position()
        tolerance = self.battle_tolerance()

        if self._resume_reposition:
            logger.warning('Death/disconnect pending, skip auto-walk')
            return False

        location = self.map_current_location()
        if (location and self.map_name_match(location[0], map_name)
                and abs(location[1] - position[0]) <= tolerance
                and abs(location[2] - position[1]) <= tolerance):
            logger.info('Already at battle position')
            return True

        try:
            actual = self.map_go_to_coordinate(
                position[0], position[1], map_name=map_name,
                timeout=self.MAP_WALK_TIMEOUT, tolerance=tolerance)
        except TaskFailed as error:
            logger.warning(f'Minimap coordinate navigation failed: {error}')
            return False

        if self._resume_reposition:
            logger.warning('Auto-walk interrupted by death/disconnect')
            return False
        logger.info(f'Arrived at battle position {actual[0]} {actual[1]},{actual[2]}')
        return True

    # ---------------------------------------------------------------- 打怪主循环
    def battle_loop(self) -> None:
        logger.hr('野外雪人循环打怪')
        names = self.target_names()
        # 技能位只在本场任务第一次锁到目标时识别一次，之后目标丢失/换怪都复用。
        # battle_skill_loop 内部会在冷却结束后重认 pending 技能位，这里取回更新后的顺序。
        slots = []
        while True:
            if self.stop_for_others():
                self._yielded = True
                return
            if self.stop_because_disabled():
                self._stopped_by_user = True
                return

            if self._resume_reposition:
                reset = self._reset_recover_after_resume
                self._resume_reposition = False
                self._reset_recover_after_resume = False
                if not self.ensure_in_map_and_position(reset_recover=reset):
                    raise GameStuckError('Cannot return to Wild Snowman battle position')
                continue

            if self.need_recover():
                self.do_heal_and_repair()
                continue

            if not self.acquire_target(names):
                self.device.sleep(1)
                if self._target_fail_count % self.TARGET_WARN_AFTER == 0:
                    if not self.return_to_battle_position():
                        self._resume_reposition = True
                continue

            if not slots:
                slots = self.recognize_skill_slots()
            # 重认 pending 技能位后使用最新顺序；没有更新时保留上一次的缓存
            slots = self.battle_current_slots() or slots
            if slots:
                reason = self.battle_skill_loop(
                    slots,
                    check_stop=self.battle_stop_reason,
                    target_check=lambda image: self.battle_target_ok(names, image),
                    include_attack=self.battle_use_normal_attack(),
                    attack_interval=self.NORMAL_ATTACK_INTERVAL,
                    poll_interval=self.SKILL_COOLDOWN_POLL,
                    on_cast=self.on_cast,
                    on_target_lost=self.battle_on_target_lost(
                        names, abort_check=lambda: self._resume_reposition),
                )
            else:
                reason = self.normal_attack_until_lost(names)

            if reason == 'yield':
                self._yielded = True
                return
            if reason == 'disabled':
                self._stopped_by_user = True
                return
            if reason == 'recover':
                self.do_heal_and_repair()
                continue
            if reason == 'reposition':
                # 摇杆回位失败 / 已离开地图：下一轮走自动寻路回打怪点
                continue
            # target_lost / timeout：重新锁目标继续
            self.device.sleep(0.2)

    def battle_stop_reason(self, image=None):
        """battle_skill_loop 的停止条件：让路 / 任务被关闭 / 该修理了 / 该回位了"""
        if self._resume_reposition:
            return 'reposition'
        if self.stop_for_others():
            return 'yield'
        if self.stop_because_disabled():
            return 'disabled'
        if self.need_recover():
            return 'recover'
        return None

    def stop_for_others(self) -> bool:
        """检测到更高优先级的正常任务到期时让路"""
        if not self._yield_timer.reached_and_reset():
            return False
        if self.higher_priority_task_due():
            logger.info('检测到更高优先级任务到期，野外雪人让路')
            return True
        return False

    def stop_because_disabled(self) -> bool:
        """运行中检测 UI 是否关闭了本任务"""
        if not self._config_timer.reached_and_reset():
            return False
        if not self.config.should_reload():
            return False
        self.config.reload()
        self.config.start_watching()
        self.battle_config_cache_clear()
        if not self.config.wild_snowman.scheduler.enable:
            logger.info('野外雪人已在配置中关闭，停止任务')
            return True
        return False

    # ---------------------------------------------------------------- 锁目标 / 技能
    def acquire_target(self, names: list) -> bool:
        """锁一个白名单目标（陵鱼 / 陵魚 / 可爱雪人 / 可愛雪人）"""
        if self.ui_ensure_target_locked(
                target_name=names,
                action=ACTION_ATTACK,
                max_switch=self.TARGET_SWITCH_MAX,
                timeout=self.TARGET_SWITCH_TIMEOUT,
                abort_check=lambda: self._resume_reposition):
            self._target_fail_count = 0
            return True

        if self._resume_reposition:
            # 死亡复活/掉线已置位：不在复活点原地切目标，交回主循环立即走位
            return False

        self._target_fail_count += 1
        if self._target_fail_count % self.TARGET_WARN_AFTER == 1:
            logger.warning(f'连续 {self._target_fail_count} 次没有锁到白名单目标 {names}，'
                           f'继续等待刷新')
        return False

    def battle_target_ok(self, names: list, image=None) -> bool:
        """
        放技能/普攻前确认「锁着的就是白名单目标」，避免打到路过的玩家。

        每次都现查名字（不缓存）：玩家可攻击，只判「可攻击」会在雪人死后 /
        游戏自动转锁到玩家时继续打偏。名字读不出来时按原目标处理，
        别因一次 OCR 抖动就停手。
        """
        if not self.ui_target_attackable(image=image):
            return False
        name = self.ui_target_name()
        if not name:
            return True
        if any(self.ocr_name_match(name, expected) for expected in names):
            return True
        logger.warning(f'锁着的目标变成 [{name}]，不在白名单 {names}，停止放技能')
        return False

    def recognize_skill_slots(self) -> list:
        """识别技能位；认不出来就不盲点技能位，退回普通攻击"""
        for attempt in range(1, self.SLOT_RECOGNIZE_ATTEMPTS + 1):
            self.screenshot()
            slots = self.battle_plan_slots()
            if slots:
                logger.info(f'本场技能位：{[slot + 1 for slot in slots]}')
                return slots
            logger.warning(f'技能位没识别出来（第 {attempt}/{self.SLOT_RECOGNIZE_ATTEMPTS} 次）')
            self.device.sleep(self.SLOT_RECOGNIZE_WAIT)
        return []

    def normal_attack_until_lost(self, names: list) -> str:
        """技能位识别失败时，只用普通攻击，直到目标丢失/变玩家或需要让路/修理"""
        logger.warning('技能位未识别，使用普通攻击继续打怪')
        while True:
            reason = self.battle_stop_reason()
            if reason:
                return reason
            image = self.screenshot()
            if not self.battle_target_ok(names, image):
                return 'target_lost'
            self.battle_click_attack()
            self.device.sleep(self.NORMAL_ATTACK_INTERVAL)

    def on_cast(self, _casts: int) -> None:
        """每成功释放若干次技能检查一次位置，防止被怪推离打怪点"""
        self._casts += 1
        if self._casts % self.RECOVER_EVERY_CASTS:
            return
        if not self.return_to_battle_position():
            self._resume_reposition = True
        logger.attr('打怪进度', f'casts={self._casts}')

    def return_to_battle_position(self) -> bool:
        """战斗中回到基准坐标（先摇杆微调，失败再用小地图坐标寻路兜底）"""
        map_name = self.map_name()
        location = self.map_move_read_pos()
        if location is None:
            return False
        if not self.map_name_match(location[0], map_name):
            return False
        x, y = self.battle_position()
        tolerance = self.battle_tolerance()
        distance = math.hypot(location[1] - x, location[2] - y)
        if distance <= tolerance:
            return True
        logger.info(f'位置漂移到 {location[0]}{location[1]},{location[2]}'
                    f'（距离 {distance:.1f}），先用摇杆回打怪点')
        if self.map_move_to(x, y, map_name=map_name,
                            tolerance=tolerance, timeout=30,
                            ensure_main=False, calibrate=False):
            return True
        # 摇杆可能被地形/碰撞卡住，自动寻路不会被卡；改用小地图坐标寻路
        logger.warning('摇杆回位失败，改用小地图坐标寻路回打怪点')
        return self.walk_to_battle_position()

    # ---------------------------------------------------------------- 修理 / 治疗
    def need_recover(self) -> bool:
        interval = int(self.current_config().heal_repair_interval_minutes)
        return datetime.now() - self._last_recover >= timedelta(minutes=interval)

    def do_heal_and_repair(self) -> None:
        """找万事通治疗 -> 背包修理装备 -> 回打怪点，并重置计时"""
        logger.hr('野外雪人定时治疗/修理')
        try:
            if self.dungeon_heal(retries=1):
                logger.info('治疗完成')
            else:
                logger.warning('治疗失败，继续修理')
            if self.dungeon_repair_equipment():
                logger.info('修理装备完成')
            else:
                logger.warning('修理装备失败，继续打怪')
        finally:
            self.device.sleep(0.5)
            # 治疗/修理过程中死亡则重置计时；掉线则保留原计时，回位后马上补做
            if self._resume_reposition:
                reset_recover = self._reset_recover_after_resume
            else:
                reset_recover = True
            if not self.ensure_in_map_and_position(reset_recover=reset_recover):
                raise GameStuckError('Cannot return to battle position after heal/repair')

    def dungeon_find_healer(self) -> bool:
        """域外灵岛的万事通在小地图右上角，走专属点位；其它地图沿用副本逻辑"""
        # 战斗中调用时先回主页面，否则读不到右上角地点，也会点不到小地图
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            logger.warning('Main page does not appear')
            return False
        self.map_close_main_popup()
        location = self.map_current_location()
        if location and self.map_name_match(location[0], self.HEALER_MAP):
            return self.find_healer_in_lingdao()
        return super().dungeon_find_healer()

    def find_healer_in_lingdao(self) -> bool:
        """小地图右上角点万事通，等人物自动走过去；NPC 不会自动弹对话框"""
        logger.info('前往域外灵岛万事通')
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            logger.warning('Main page does not appear')
            return False
        self.map_close_main_popup()
        if not self.dungeon_click_minimap_target(self.MINIMAP_CORNER,
                                                 self.C_LINGDAO_HEALER_NPC):
            logger.warning('Click 万事通 failed')
            return False
        self.map_wait_walk_arrive(stable_time=2, timeout=60)
        return self.dungeon_wait_healer_dialog()

    def dungeon_wait_healer_dialog(self, timeout: int = None) -> bool:
        """
        在域外灵岛轮询锁定的目标：
        - 没有目标就先点「目标」按钮锁定
        - 目标不是万事通就继续切换目标，直到锁到万事通
        - 锁到万事通后手动点攻击/交互键打开对话框
        """
        timeout = timeout if timeout is not None else self.DUNGEON_HEALER_DIALOG_TIMEOUT
        timer = Timer(timeout).start()
        interact_timer = Timer(self.DUNGEON_HEALER_INTERACT_AFTER).start()
        interactions = 0
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_DUNGEON_HEALER_DIALOG):
                logger.info('Healer dialog appeared')
                return True
            if self._resume_reposition:
                logger.warning('Death/disconnect while waiting healer dialog')
                return False
            if interactions < self.DUNGEON_HEALER_MAX_INTERACT and interact_timer.reached():
                target = self.ui_target_name()
                if not target:
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
                    # 锁到的是怪就继续切换，不要傻等 30 秒
                    logger.info('Target is not healer, switch target')
                    self.ui_close_menu()
                    self.ui_lock_target()
                    interact_timer.reset()
            self.device.sleep(1)
        logger.warning('Healer dialog does not appear')
        return False

    # ---------------------------------------------------------------- 死亡 / 掉线
    def handle_death_resume(self) -> None:
        """
        死亡复活：重新前往配置的打怪坐标，并重置修理/治疗计时

        全局 handle_death 已负责点「返回村子」复活，这里只记录需要重新出发。
        """
        logger.warning('野外雪人死亡复活，重新前往打怪点并重置修理计时')
        self.device.sleep(1)
        self._resume_reposition = True
        self._reset_recover_after_resume = True

    def handle_disconnect_resume(self) -> None:
        """
        掉线重启：重新前往配置的打怪坐标，但不重置修理/治疗计时
        """
        logger.warning('野外雪人掉线重启，重新前往打怪点（保留修理计时）')
        self.device.sleep(1)
        self._resume_reposition = True
        self._reset_recover_after_resume = False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        t = ScriptTask(config, device)
        t.run()
    except TaskEnd:
        pass
