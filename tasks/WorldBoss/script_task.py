# This Python file uses the following encoding: utf-8
from datetime import datetime, time, timedelta

import cv2

from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.Battle.target_action import ACTION_ATTACK
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_activity_world_boss
from tasks.WorldBoss.assets import WorldBossAssets

# 世界首领：目标地图与坐标来自游戏内「寻」的自动寻路终点（实测值）
# TODO: 天师的幻影（全服 boss，12:00/22:00）尚未接入；它是全服地图的特殊 boss，
#       需要确认专属地图/机制后再加入 BOSS_LIST 和配置时间。
BOSS_LIST = [
    {
        'key': 'manchui',
        'name': '蛮锤',
        'target_map': '牧野',
        'target_coord': (272, 70),
        'times': [('time_09', time(9, 0)), ('time_11', time(11, 0)), ('time_13', time(13, 0))],
    },
    {
        'key': 'wuqing',
        'name': '无情',
        'target_map': '万剑冢',
        'target_coord': (124, 372),
        'times': [('time_10', time(10, 0)), ('time_12', time(12, 0)), ('time_19', time(19, 0))],
    },
    {
        'key': 'julian',
        'name': '巨镰',
        'target_map': '骨牢',
        'target_coord': (216, 184),
        'times': [('time_15', time(15, 0)), ('time_17', time(17, 0)), ('time_19', time(19, 0))],
    },
    {
        'key': 'fuwang',
        'name': '斧王',
        'target_map': '忘川',
        'target_coord': (219, 416),
        'times': [('time_14', time(14, 0)), ('time_18', time(18, 0)), ('time_22', time(22, 0))],
    },
    {
        'key': 'xiewang',
        'name': '蝎王',
        'target_map': '沼泽',
        'target_coord': (259, 345),
        'times': [('time_21', time(21, 0))],
    },
]

# 首领名称素材宽度 68，名称中心相对素材左边缘为 34
# 「寻」按钮 x = 名称中心 + 63.5，y = 576
SEEK_OFFSET_X = 98
SEEK_OFFSET_Y = 576
ARRIVE_TOLERANCE = 20
# 自动寻路卡住判定：坐标连续多少秒没变化就重新点「寻」重启寻路
ARRIVE_STALL_SECONDS = 10


class WorldBossDeath(Exception):
    """WorldBoss 内部死亡：复活后由当前事件流程捕获并重试对应阶段"""

    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


class WorldBossFinished(Exception):
    """结束模板命中：由截图钩子在任何阶段抛出，事件流程捕获后标记该 boss 已完成"""

    def __init__(self, key: str = ''):
        super().__init__(key)
        self.key = key


class ScriptTask(GameUi, WorldBossAssets):
    """
    世界首领：按配置的时间段提前出发，自动寻路到首领位置
    锁定目标后按任务预设技能队列攻击，同时轮询聊天栏结束模板判定 boss 是否已结束。
    同一时段两个 boss：先打配置选中的 A 共 N 个技能，再打 B；全程同时轮询双方结束模板，
    B 结束后 A 还没结束就回 A 继续打。
    整点后最多参与 event_window（10 分钟）：没锁定到目标、也没出结束模板就到点结束。

    两个跨阶段的机制都由异常抛给事件流程统一处理：
    - 阵亡：handle_death 复活后抛 WorldBossDeath，按死亡阶段续跑（寻路 / 锁定 / 攻击）；
    - 结束：首领刷新后 watch_finish_start 开启监听，之后任何一次截图命中结束模板
      都会抛 WorldBossFinished —— 锁定、攻击、复活、重新寻路都不会漏掉这条聊天消息。

    TODO:
      - 天师的幻影（全服 boss，12:00/22:00）尚未接入，见 BOSS_LIST 顶部说明。
      - 双 boss 期间被其他玩家攻击，可能打断回主页面/地图传送/「寻」的自动寻路；
        目前未处理，后续需要检测受击/战斗状态并重试地图移动与切换。
    """

    # 首领出现后最多参与多久（从整点算起）：没锁到目标、也没出结束模板，到点结束
    event_window = timedelta(minutes=10)
    # 目标名识别：灰度放大倍数（真机实测 2x/3x 最好，取置信度高的一份）
    target_name_scales = (2, 3)
    # 目标名识别置信度下限，低于此值视为没读到
    target_name_score = 0.5
    # 锁定目标时点「目标」的间隔（秒）；锁定循环一直跑到整点后 event_window
    TARGET_LOCK_SWITCH_INTERVAL = 1.0
    # 同一时段两个 boss：锁定某个 boss 最多尝试多久，超时后按流程切换到另一个
    PAIR_LOCK_TIMEOUT = 30
    # 每个技能最多尝试几次（含第一次）、等冷却超时/轮询间隔
    SKILL_MAX_ATTEMPTS = 3
    SKILL_COOLDOWN_TIMEOUT = 1.2
    SKILL_COOLDOWN_POLL = 0.1
    # 锁定阶段连续没有目标多久，提示「首领可能已被击败」（秒）
    NO_TARGET_HINT = 20
    # 目标丢失后等结束模板出现的时间/轮询间隔（秒）
    FINISH_TEMPLATE_TIMEOUT = 5.0
    FINISH_TEMPLATE_POLL = 0.2
    # 目标丢失后原地等结束消息的时间（秒）
    #
    # 目标消失 = 首领大概率已死，但结束消息可能慢几秒才刷出来。
    # 这段时间必须**原地等**（不传送、不点寻、不开活动页）—— 一旦做这些操作，
    # 聊天栏会被新消息顶走，结束消息一没就再也认不出来，事件只能空耗到超时。
    FINISH_WAIT_AFTER_TARGET_LOST = 30.0
    # 结束模板分数达到该值直接判定，不再做 OCR 确认
    FINISH_OCR_SKIP_SCORE = 0.98
    # 结束确认后等「首领归属奖」弹窗的时间/轮询间隔（秒）
    FINISH_REWARD_WINDOW = 2.0
    FINISH_REWARD_POLL = 0.2
    # 本次运行已完成的 {boss_key 时间} 事件（阵亡重跑时恢复）
    done_events: set = None
    # 当前阶段：navigate=寻路/锁定，attack=攻击（死亡时用于日志和恢复）
    _death_stage: str = 'navigate'

    # ---------------------------------------------------------------- 结束监听
    # 结束模板是聊天栏系统消息，会随新消息往上滚走。从首领刷新到「结束 / 超时」之间，
    # 任何一次截图都要顺带查一次：锁定、攻击、阵亡复活、重新寻路都不再需要各自记得轮询。
    _watch_boss: dict = None
    _watch_rule = None
    _watch_deadline: datetime = None
    # 监听期间临时静音（check_finish / handle_finish 自己要看模板时用，避免钩子抢跑抛异常）
    _watch_muted: bool = False

    def watch_finish_start(self, boss: dict, finish_rule, deadline: datetime) -> None:
        """首领刷新后开启结束监听，直到命中、超时或手动停止"""
        self._watch_boss = boss
        self._watch_rule = finish_rule
        self._watch_deadline = deadline
        self._watch_muted = False
        if finish_rule is None:
            logger.warning(f'{boss["name"]} 缺少结束模板素材，无法监听首领结束')

    def watch_finish_stop(self) -> None:
        """关闭结束监听（命中、切换 boss、任务收尾时调用）"""
        self._watch_boss = None
        self._watch_rule = None
        self._watch_deadline = None
        self._watch_muted = False

    def screenshot(self):
        """所有截图统一入口：截完先做全局处理，再查一次结束模板"""
        image = super().screenshot()
        self.watch_finish_check(image)
        return image

    def watch_finish_check(self, image) -> None:
        """
        监听中就用这一帧判断首领是否结束，命中直接抛 WorldBossFinished

        为什么用异常：结束可能发生在任何阶段（锁定、攻击、死亡复活、重新寻路），
        抛异常让当前深层层级直接退出，统一由事件流程标记完成并处理归属奖，
        比让每个循环各自 return 'finished' 更难漏。
        """
        boss = self._watch_boss
        if boss is None or self._watch_muted or self._watch_rule is None:
            return
        if self._watch_deadline is not None and datetime.now() >= self._watch_deadline:
            return
        if self.finish_confirmed(boss, self._watch_rule, image):
            logger.info(f'{boss["name"]} finish confirmed while watching')
            raise WorldBossFinished(boss['key'])

    def run(self) -> None:
        self.reset_records()
        events = self.current_events()
        if not events:
            logger.info(f'No world boss event now (event window '
                        f'{int(self.event_window.total_seconds() // 60)} min), '
                        f'skip and schedule next run')
            self.clear_task_record()
            self.schedule_next_event()
            raise TaskEnd('WorldBoss')

        self.done_events = set()
        if self.restore_task_record():
            logger.attr('Restored done events', ', '.join(sorted(self.done_events)) or '-')

        for event_time, group in self.group_events(events):
            pending = [boss for boss in group
                       if self.event_key_for(boss, event_time) not in self.done_events]
            if not pending:
                logger.info(f'Event {event_time:%H:%M} all bosses already finished, skip')
                continue

            logger.hr(f"World boss {event_time:%H:%M} ({len(pending)} boss)")
            try:
                if len(pending) >= 2:
                    # 同一时段两个 boss 走双 boss 流程；>2 时先处理前两个，其余按单 boss 顺序补
                    self.simultaneous_flow(pending, event_time)
                else:
                    self.world_boss_flow(pending[0], event_time)
            except WorldBossFinished as e:
                # 理论上流程内部会捕获；这里兜底层，避免监听异常直接打死任务
                logger.info(f'Boss {e.key} finished (watched, unhandled)')
                self.watch_finish_stop()
                for boss in group:
                    if boss['key'] == e.key:
                        self.mark_event_done(boss, event_time)
                        break
            except WorldBossDeath as e:
                # 理论上流程内部会捕获；这里兜底层，避免死亡异常直接打死任务
                logger.warning(f'Unhandled death during {e.stage}, rerun task')
                self.rerun_task()

        self.watch_finish_stop()
        self.clear_task_record()
        self.schedule_next_event()
        raise TaskEnd('WorldBoss')

    # ---------------------------------------------------------------- 进度记录
    def save_task_record(self) -> dict:
        """
        阵亡重跑时保留本次运行已完成的事件，避免重打已经结束的 boss
        """
        return {'done_events': sorted(self.done_events or ())}

    def load_task_record(self, record: dict) -> bool:
        """
        恢复已完成事件集合；旧版单事件记录不再兼容
        """
        events = record.get('done_events') or []
        if not events:
            return False
        self.done_events = {str(event) for event in events}
        return True

    @staticmethod
    def event_key_for(boss: dict, event_time: datetime) -> str:
        return f"{boss['key']} {event_time.strftime('%Y-%m-%d %H:%M')}"

    def mark_event_done(self, boss: dict, event_time: datetime) -> None:
        if self.done_events is None:
            self.done_events = set()
        self.done_events.add(self.event_key_for(boss, event_time))

    @staticmethod
    def group_events(events: list) -> list:
        """
        按事件时间分组：[(event_time, [boss, ...]), ...]
        同一时间刷新的多个 boss 会在同一组里（current_events 已按时间排序）
        """
        groups = []
        for boss, event_time in events:
            if groups and groups[-1][0] == event_time:
                groups[-1][1].append(boss)
            else:
                groups.append((event_time, [boss]))
        return groups

    def world_boss_flow(self, boss: dict, event_time: datetime) -> None:
        """
        一次完整的首领流程：主界面出发 -> 寻路 -> 等到刷新 -> 锁定 -> 预设技能攻击

        整点后 event_window（10 分钟）是总 deadline：没锁到目标、也没出结束模板就到点结束；
        战斗中途目标丢失但没出结束模板，会在剩余时间内重新锁定继续打。
        """
        deadline = event_time + self.event_window
        if datetime.now() >= deadline:
            logger.warning(f"{boss['name']} event window already passed: {deadline:%H:%M:%S}")
            return

        finish_rule = self.finish_rule(boss)
        while datetime.now() < deadline:
            if self.event_key_for(boss, event_time) in (self.done_events or set()):
                return
            try:
                self._death_stage = 'navigate'
                self.prepare_boss_map(boss)
                self.enter_world_boss_page()
                if not self.seek_boss(boss):
                    logger.warning(f"Unable to seek {boss['name']}")
                    return

                remaining = max(1, int((deadline - datetime.now()).total_seconds()))
                arrive = self.wait_until_arrive(boss, timeout=min(180, remaining),
                                                finish_rule=finish_rule)
                if arrive == 'finished':
                    self.mark_event_done(boss, event_time)
                    self.handle_finish(boss)
                    return
                if arrive != 'arrived':
                    logger.warning(f"Did not arrive at {boss['target_map']}{boss['target_coord']}")

                while datetime.now() < deadline:
                    self._death_stage = 'navigate'
                    lock_result = self.lock_boss(boss, event_time, finish_rule, deadline)
                    if lock_result == 'finished':
                        self.mark_event_done(boss, event_time)
                        self.handle_finish(boss)
                        return
                    if lock_result != 'locked':
                        logger.warning(f"Unable to lock {boss['name']} before {deadline:%H:%M:%S}")
                        return

                    self._death_stage = 'attack'
                    result = self.attack_boss(boss, finish_rule, deadline)
                    if result == 'finished':
                        self.mark_event_done(boss, event_time)
                        self.handle_finish(boss)
                        return
                    if result == 'target_lost':
                        logger.info(f"{boss['name']} target lost before finish, retry until {deadline:%H:%M:%S}")
                        continue
                    return

                logger.warning(f"{boss['name']} engagement timeout at {deadline:%H:%M:%S}")
                return
            except WorldBossFinished:
                logger.info(f"{boss['name']} finished (watched)")
                self.watch_finish_stop()
                self.mark_event_done(boss, event_time)
                self.handle_finish(boss)
                return
            except WorldBossDeath as e:
                logger.warning(f'Revived during {e.stage}, re-path to {boss["name"]}')
                # 复活/重新寻路这几十秒没有战斗循环，结束消息最容易在这段被刷走
                if self.check_finish(boss, finish_rule, 'revive'):
                    self.mark_event_done(boss, event_time)
                    self.handle_finish(boss)
                    return
                continue

        logger.warning(f"{boss['name']} engagement timeout at {deadline:%H:%M:%S}")

    # ---------------------------------------------------------------- 双 boss
    def pair_order(self, pair: list) -> tuple:
        """
        返回 (先打, 后打)。配置里选中的 boss 在 pair 中就用它，否则按事件顺序。
        """
        selected = self.config.world_boss.world_boss_config.first_boss
        selected = getattr(selected, 'value', selected)
        for boss in pair:
            if boss['key'] == selected:
                other = next(item for item in pair if item is not boss)
                return boss, other
        return pair[0], pair[1]

    @staticmethod
    def pair_all_done(state: dict) -> bool:
        return bool(state) and all(state.values())

    def pair_update_finish(self, image, pair: list, state: dict, event_time: datetime) -> list:
        """
        轮询 pair 里所有未完成 boss 的结束模板（同一张截图，模板命中后再按需 OCR）

        新确认的 boss 会写入 done_events，并返回给调用方做奖励面板处理。
        """
        newly = []
        for boss in pair:
            key = boss['key']
            if state.get(key):
                continue
            if self.finish_confirmed(boss, self.finish_rule(boss), image):
                state[key] = True
                self.mark_event_done(boss, event_time)
                logger.info(f'{boss["name"]} finished (double boss)')
                newly.append(boss)
        return newly

    def wait_pair_finish(self, boss: dict, pair: list, state: dict,
                         event_time: datetime, deadline: datetime,
                         timeout: float) -> str:
        """
        原地等待结束消息（不移动、不传送、不点寻）

        结束消息在聊天栏里，新消息会把旧消息顶走，所以消息要趁早认；
        一旦去做导航类的操作（传送/开活动页/点寻），消息就可能被刷没了。

        :return: 'finished' / 'timeout'
        """
        wait = min(timeout, max(0.0, (deadline - datetime.now()).total_seconds()))
        if wait <= 0:
            return 'timeout'
        logger.info(f'原地等待结束消息（最多 {wait:.0f}s）')
        timer = Timer(wait).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            newly = self.pair_update_finish(self.device.image, pair, state, event_time)
            self.handle_pair_new(newly, boss)
            if state.get(boss['key']) or self.pair_all_done(state):
                return 'finished'
            self.device.sleep(self.FINISH_TEMPLATE_POLL)
        return 'timeout'

    def handle_pair_new(self, newly: list, current_boss: dict,
                        allow_reward: bool = True) -> None:
        """
        只给「当前正在打的 boss」处理首领归属奖弹窗

        pair 里另一个 boss 的结束模板只用于标记它已死；离开它的地图后归属不再属于自己，
        即使模板出现也不处理奖励面板。
        """
        for boss in newly:
            if boss['key'] != current_boss['key']:
                logger.info(f'{boss["name"]} finished while current is '
                            f'{current_boss["name"]}, skip reward popup')
                continue
            if not allow_reward:
                logger.info(f'{boss["name"]} finished before engagement, skip reward popup')
                continue
            self.handle_finish(boss)

    def pair_lock_boss(self, boss: dict, pair: list, state: dict, event_time: datetime,
                       deadline: datetime, lock_timeout: float) -> str:
        """
        双 boss 场景锁定：锁定期间同时轮询双方结束模板；
        最多尝试 lock_timeout 秒，超时返回 lock_timeout 由上层决定切换。
        :return: 'locked' / 'finished' / 'all_done' / 'lock_timeout' / 'timeout'
        """
        logger.hr(f"Lock {boss['name']} (double boss)")
        self.wait_until_spawn(event_time)
        self.watch_finish_start(boss, self.finish_rule(boss), deadline)
        lock_deadline = deadline
        if lock_timeout:
            lock_deadline = min(deadline, datetime.now() + timedelta(seconds=lock_timeout))
        keywords = self.target_keywords(boss)
        logger.attr('Target keywords', ', '.join(keywords))
        self.collapse_menu()

        # 连续锁不到目标的次数：用于降噪（只在第一次打 INFO）和「可能已被打死」提示
        no_target_rounds = 0
        no_target_hint = False
        no_target_timer = Timer(self.NO_TARGET_HINT).start()

        while datetime.now() < lock_deadline:
            self.reset_records()
            self.screenshot()
            image = self.device.image

            newly = self.pair_update_finish(image, pair, state, event_time)
            self.handle_pair_new(newly, boss)
            if state.get(boss['key']):
                return 'finished'
            if self.pair_all_done(state):
                return 'all_done'

            if self.ui_target_locked(image=image):
                no_target_rounds = 0
                action = self.ui_target_action(image=image)
                if action == ACTION_ATTACK:
                    name = self.ui_target_name(scales=self.target_name_scales,
                                               min_score=self.target_name_score)
                    if any(self.ocr_name_match(name, word) for word in keywords):
                        logger.info(f'Target locked and matched: {name}')
                        return 'locked'
                    logger.info(f'Target [{name or "?"}] not in {keywords}, switch target')
                else:
                    logger.info(f'Target action is {action}, need attack, switch target')
            else:
                no_target_rounds += 1
                # 长时间锁不到目标时这行会每 1.3 秒刷一次，只保留第一次
                if no_target_rounds == 1:
                    logger.info('No target locked, click target button')
                else:
                    logger.debug(f'No target locked (round {no_target_rounds}), click target button')
                if not no_target_hint and no_target_timer.reached():
                    no_target_hint = True
                    logger.warning(f'No target near {boss["name"]} for {self.NO_TARGET_HINT}s, '
                                   f'it may already be finished; '
                                   f'still trying until {lock_deadline:%H:%M:%S}')

            self.appear_then_click(self.I_TARGET_BUTTON, action=self.C_TARGET_BUTTON_CLICK)
            self.device.sleep(self.TARGET_LOCK_SWITCH_INTERVAL)

        self.screenshot()
        newly = self.pair_update_finish(self.device.image, pair, state, event_time)
        self.handle_pair_new(newly, boss)
        if state.get(boss['key']):
            return 'finished'
        if self.pair_all_done(state):
            return 'all_done'
        if datetime.now() >= deadline:
            return 'timeout'
        logger.warning(f'{boss["name"]} lock timeout after {lock_timeout}s, switch')
        return 'lock_timeout'

    def pair_attack_loop(self, boss: dict, pair: list, state: dict, event_time: datetime,
                         deadline: datetime, max_skills: int = None,
                         cast_counter: dict = None) -> str:
        """
        双 boss 攻击：每轮同时轮询双方结束模板
        - max_skills=N：成功释放 N 个技能后返回 skill_limit；
        - max_skills=None：打到当前 boss 结束/目标丢失/超时。

        cast_counter 跨死亡重试累计「先攻击的技能数」：
        计数存在调用方传来的可变 dict 里，死亡抛异常中断本次攻击后，
        重试进入本方法会从上一次的累计值继续，不会重新从 0 打 N 个。
        """
        logger.hr(f"Attack {boss['name']} (double boss)")
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return 'timeout'

        used = 0
        if max_skills is not None and cast_counter is not None:
            used = int(cast_counter.get('casts') or 0)
            if used >= max_skills:
                logger.info(f'已释放 {used}/{max_skills} 个技能，无需再攻击')
                return 'skill_limit'
        skill_left = None if max_skills is None else max_skills - used
        if skill_left is not None:
            logger.attr('剩余技能数', skill_left)

        def record_cast(total: int) -> None:
            if cast_counter is not None:
                cast_counter['casts'] = used + total

        self.screenshot()
        slots = self.battle_plan_slots()
        logger.info(f'技能顺序：{[slot + 1 for slot in slots]}')
        if max_skills:
            logger.attr('先攻击技能数', f'{max_skills}（已用 {used}，本轮最多 {skill_left}）')

        def stop_check(image):
            newly = self.pair_update_finish(image, pair, state, event_time)
            self.handle_pair_new(newly, boss)
            if state.get(boss['key']):
                return 'finished'
            if self.pair_all_done(state):
                return 'all_done'
            return None

        result = self.battle_skill_loop(
            slots,
            check_stop=stop_check,
            target_check=lambda image: self.ui_target_attackable(image=image),
            timeout=remaining,
            max_attempts=self.SKILL_MAX_ATTEMPTS,
            cooldown_timeout=self.SKILL_COOLDOWN_TIMEOUT,
            poll_interval=self.SKILL_COOLDOWN_POLL,
            max_skills=skill_left,
            on_cast=record_cast if cast_counter is not None else None,
        )

        if result == 'target_lost':
            # 目标消失 = 首领大概率已死，但结束消息可能慢几秒才刷出来。
            # 这里原地等消息（不传送、不点寻）：一旦做导航类操作，聊天消息会被顶走，
            # 消息一没就再也认不出来，事件只能空耗到超时。
            result = self.wait_pair_finish(boss, pair, state, event_time, deadline,
                                           timeout=self.FINISH_WAIT_AFTER_TARGET_LOST)
            if result != 'finished':
                # 等了很久还没有消息：按「已被打死」收尾，避免反复传送把消息冲掉
                logger.warning(f'{boss["name"]} 目标消失且没等到结束消息，按已结束收尾')
                state[boss['key']] = True
                self.mark_event_done(boss, event_time)
                self.handle_pair_new([boss], boss)
                result = 'finished'

        if result == 'timeout':
            self.screenshot()
            newly = self.pair_update_finish(self.device.image, pair, state, event_time)
            self.handle_pair_new(newly, boss)
            if state.get(boss['key']) or self.pair_all_done(state):
                result = 'finished'
        return result

    def pair_fight_boss(self, boss: dict, pair: list, state: dict, event_time: datetime,
                        deadline: datetime, max_skills: int = None,
                        lock_timeout: float = None, cast_counter: dict = None) -> str:
        """
        双 boss 场景下单次“前往 -> 锁定 -> 攻击”

        cast_counter 用于跨死亡重试保留已释放的技能数（见 pair_attack_loop）。
        :return: 'finished' / 'all_done' / 'skill_limit' / 'target_lost' /
                 'lock_timeout' / 'navigate_failed' / 'timeout'
        """
        while datetime.now() < deadline:
            if state.get(boss['key']):
                return 'finished'
            try:
                self._death_stage = 'navigate'
                self.screenshot()
                newly = self.pair_update_finish(self.device.image, pair, state, event_time)
                self.handle_pair_new(newly, boss, allow_reward=False)
                if state.get(boss['key']):
                    return 'finished'
                if self.pair_all_done(state):
                    return 'all_done'

                self.prepare_boss_map(boss)
                try:
                    self.enter_world_boss_page()
                    if not self.seek_boss(boss):
                        logger.warning(f"Unable to seek {boss['name']}")
                        return 'navigate_failed'
                    remaining = max(1, int((deadline - datetime.now()).total_seconds()))
                    arrive = self.wait_until_arrive(boss, timeout=min(180, remaining),
                                                    finish_rule=self.finish_rule(boss))
                    if arrive == 'timeout':
                        logger.warning(f"Did not arrive at {boss['target_map']}{boss['target_coord']}")
                except (GameStuckError, GamePageUnknownError, GameNotRunningError) as e:
                    logger.warning(f"Navigate {boss['name']} failed: {e}")
                    return 'navigate_failed'

                self.screenshot()
                newly = self.pair_update_finish(self.device.image, pair, state, event_time)
                self.handle_pair_new(newly, boss, allow_reward=False)
                if state.get(boss['key']):
                    return 'finished'
                if self.pair_all_done(state):
                    return 'all_done'

                lock_result = self.pair_lock_boss(boss, pair, state, event_time, deadline,
                                                  lock_timeout or self.PAIR_LOCK_TIMEOUT)
                if lock_result != 'locked':
                    return lock_result
                self._death_stage = 'attack'
                return self.pair_attack_loop(boss, pair, state, event_time, deadline,
                                             max_skills, cast_counter)
            except WorldBossFinished as e:
                # 结束消息可能是「另一个 boss」的：监听状态还指着上一个 boss，
                # 消息在切回来的这一帧才冒出来。必须按 e.key 归因 ——
                # 记到当前 boss 头上会让另一个 boss 永远「没打完」，
                # 于是反复传送过去再打一遍（每次传送 500 铜贝）。
                if e.key and e.key != boss['key']:
                    other = next((item for item in pair if item['key'] == e.key), None)
                    self.watch_finish_stop()
                    if other is None:
                        logger.info(f'Boss {e.key} finished (watched, unknown boss)')
                        return 'finished'
                    logger.info(f'{other["name"]} finished (watched, 当前在打 {boss["name"]})')
                    state[other['key']] = True
                    self.mark_event_done(other, event_time)
                    self.handle_finish(other)
                    return 'finished'
                logger.info(f"{boss['name']} finished (watched)")
                self.watch_finish_stop()
                state[boss['key']] = True
                self.mark_event_done(boss, event_time)
                self.handle_finish(boss)
                return 'finished'
            except WorldBossDeath as e:
                logger.warning(f'Revived during {e.stage}, return to {boss["name"]} and continue')
                continue

        return 'timeout'

    def simultaneous_flow(self, bosses: list, event_time: datetime) -> None:
        """
        同一时段两个 boss：先打配置选中的 A 共 N 个技能，再打 B 到 B 结束；
        B 结束后 A 还没结束就回 A 继续打。全程同时轮询双方结束模板。
        """
        pair = bosses[:2]
        deadline = event_time + self.event_window
        if datetime.now() >= deadline:
            logger.warning(f'Double boss event window already passed: {deadline:%H:%M:%S}')
            return

        state = {boss['key']: self.event_key_for(boss, event_time) in (self.done_events or set())
                 for boss in pair}
        if self.pair_all_done(state):
            logger.info('Both bosses already finished')
            return

        first, second = self.pair_order(pair)
        skills = max(1, int(self.config.world_boss.world_boss_config.first_boss_skills))
        logger.hr(f'Double boss {first["name"]} -> {second["name"]}')
        logger.attr('First boss', f'{first["name"]} {skills} skills')
        logger.attr('Pair state', str(state))

        # 1. 第一个 boss：打 N 个技能（中途结束则提前切换）
        # cast_counter 在 pair_fight_boss 的死亡重试之间共享，避免复活后重打 N 个
        cast_counter = {'casts': 0}
        result = self.pair_fight_boss(first, pair, state, event_time, deadline,
                                      max_skills=skills,
                                      lock_timeout=self.PAIR_LOCK_TIMEOUT,
                                      cast_counter=cast_counter)
        logger.attr('First boss result', f'{result}（已释放 {cast_counter["casts"]}/{skills}）')

        # 2. 第二个 boss：打到它结束
        while (not state[second['key']] and not self.pair_all_done(state)
               and datetime.now() < deadline):
            result = self.pair_fight_boss(second, pair, state, event_time, deadline,
                                          max_skills=None,
                                          lock_timeout=self.PAIR_LOCK_TIMEOUT)
            if state[second['key']] or self.pair_all_done(state):
                break
            if result == 'timeout':
                break
            if result in ('lock_timeout', 'target_lost', 'navigate_failed'):
                if not state[first['key']]:
                    break
                logger.info(f'First boss finished, retry second until {deadline:%H:%M:%S}')
                continue
            break

        # 3. 第二个结束后，第一个还没结束 -> 回第一个打到它的结束模板
        while (not state[first['key']] and not self.pair_all_done(state)
               and datetime.now() < deadline):
            result = self.pair_fight_boss(first, pair, state, event_time, deadline,
                                          max_skills=None,
                                          lock_timeout=self.PAIR_LOCK_TIMEOUT)
            if state[first['key']] or self.pair_all_done(state):
                break
            if result == 'timeout':
                break
            logger.info('First boss not finished, retry until deadline')

        # 4. 第一个已结束、第二个仍未结束 -> 剩余时间继续尝试第二个
        while (not state[second['key']] and state[first['key']]
               and not self.pair_all_done(state) and datetime.now() < deadline):
            result = self.pair_fight_boss(second, pair, state, event_time, deadline,
                                          max_skills=None,
                                          lock_timeout=self.PAIR_LOCK_TIMEOUT)
            if state[second['key']] or self.pair_all_done(state):
                break
            if result == 'timeout':
                break
            logger.info('Second boss not finished, retry until deadline')

        # >2 个同时 boss：前两个之后按单 boss 流程补齐（当前数据只有两个）
        for boss in bosses[2:]:
            if self.pair_all_done(state) or datetime.now() >= deadline:
                break
            if self.event_key_for(boss, event_time) in (self.done_events or set()):
                continue
            self.world_boss_flow(boss, event_time)

        unfinished = [boss['name'] for boss in pair if not state[boss['key']]]
        if unfinished:
            logger.warning(f'Double boss unfinished at {deadline:%H:%M:%S}: {unfinished}')
        else:
            logger.info('Double boss both finished')

    # ---------------------------------------------------------------- 调度
    @property
    def advance(self) -> timedelta:
        """
        提前出发的时间
        """
        advance_time = self.config.world_boss.world_boss_config.advance_time
        return timedelta(hours=advance_time.hour,
                         minutes=advance_time.minute,
                         seconds=advance_time.second)

    def iter_enabled_events(self) -> list:
        """
        遍历勾选的首领时间段
        """
        result = []
        for boss in BOSS_LIST:
            config_group = getattr(self.config.world_boss, boss['key'])
            for field, event_time in boss['times']:
                if getattr(config_group, field):
                    result.append((boss, event_time))
        return result

    def event_datetimes(self, now: datetime = None) -> list:
        """
        今天与明天的全部事件时间
        """
        now = now or datetime.now()
        today = now.date()
        result = []
        for boss, event_time in self.iter_enabled_events():
            for day in (0, 1):
                result.append((boss, datetime.combine(today + timedelta(days=day), event_time)))
        return result

    def current_events(self, now: datetime = None) -> list:
        """
        当前处于（出发时间 ~ 整点后 event_window）内的全部事件，按事件时间排序

        首领只出现 event_window（10 分钟）；任务开始时间已经超过该窗口时
        （例如 7:00 的 boss，任务 7:11 才启动），该事件不会返回，run() 会直接跳过
        本次任务并排期下一次。同一时间段可能出现多个首领（如 19:00 的无情与巨镰），
        都勾选时全部返回。
        """
        now = now or datetime.now()
        result = []
        for boss, event_datetime in self.event_datetimes(now):
            if event_datetime - self.advance <= now < event_datetime + self.event_window:
                result.append((boss, event_datetime))
        result.sort(key=lambda item: item[1])
        return result

    def schedule_next_event(self) -> None:
        """
        把下次运行时间设为下一个事件的出发时间
        """
        now = datetime.now()
        candidates = [event_datetime - self.advance
                      for _, event_datetime in self.event_datetimes()
                      if event_datetime - self.advance > now]
        if not candidates:
            logger.warning('World boss schedule is empty')
            self.set_next_run(task='WorldBoss', success=True, finish=True, server=False)
            return

        target = min(candidates)
        logger.attr('Next world boss start', target)
        self.set_next_run(task='WorldBoss', target=target, success=None, finish=True, server=False)

    # ---------------------------------------------------------------- 页面
    def prepare_boss_map(self, boss: dict) -> bool:
        """
        寻路前先确认当前地图：不在 boss 所在地就先用地图传送过去

        传送只负责跨地图，落到目标地图的传送点后仍然进活动页点「寻」，
        由游戏自动寻路走到 boss 坐标。传送失败则返回 False，调用方回退到原寻路。

        TODO: 双 boss 期间被其他玩家攻击可能打断回主页面/传送，目前未处理；
              后续需要在传送前后检测战斗/受击状态并重试。
        """
        target = boss['target_map']
        try:
            if not self.map_move_ensure_main_page(timeout=30):
                logger.warning('Cannot ensure main page before boss map check')
                return False

            # 地点名识别区很小（右上角 208x35），偶尔会读不到；重读几次再判断。
            # 读不到的时候**不能**当成「不在目标地图」—— 那会平白触发一次传送，
            # 而传送/列表页是覆盖式界面，聊天栏会被挡住，结束消息就再也认不到了。
            location = None
            for attempt in range(3):
                location = self.map_current_location()
                if location is not None:
                    break
                logger.info(f'Cannot read current location (try {attempt + 1}/3)')
                self.device.sleep(0.5)
            if location is None:
                logger.warning(f'Cannot read current location, skip teleport to {target}')
                return False
            if self.map_name_match(location[0], target):
                logger.info(f'Already on boss map {location[0]}, skip teleport')
                return True

            logger.info(f'Current map {location}, teleport to {target}')
            if self.map_teleport(target):
                logger.info(f'Teleported to boss map {target}')
                return True
            logger.warning(f'Teleport to {target} failed, fallback to seek')
            return False
        except (GameStuckError, GamePageUnknownError, GameNotRunningError) as e:
            logger.warning(f'Prepare boss map failed: {e}')
            return False

    def enter_world_boss_page(self, timeout: int = 40) -> bool:
        """
        前往活动-世界首领页面（页面寻路自动处理：展开菜单 -> 活动图标 -> 活动 tab -> 子 tab）
        同一个时间段可能有多个首领，上一个首领流程里角色已经寻路离开活动页，
        缓存的当前页面已经失效，必须先作废再重新识别，否则 ui_goto 会以为还在活动页而直接返回
        """
        logger.hr('Enter world boss page')
        self.ui_reset_current_page()
        if not self.ui_goto(page_activity_world_boss, timeout=timeout):
            raise GameStuckError('World boss page does not appear')
        return True

    def seek_boss(self, boss: dict) -> bool:
        """
        在首领列表中定位目标首领，点击其「寻」按钮
        """
        logger.hr(f"Seek {boss['name']}")
        boss_image = getattr(self, f"I_BOSS_{boss['key'].upper()}")

        # 卡片列表滚到最左，等惯性滑动结束再识别
        for _ in range(3):
            self.swipe(self.S_BOSS_SCROLL_RIGHT)
            self.device.sleep(0.6)
        self.device.sleep(0.6)

        for _ in range(5):
            self.reset_records()
            self.screenshot()
            if self.appear(boss_image):
                x = boss_image.roi_front[0] + SEEK_OFFSET_X
                logger.info(f"Click seek of {boss['name']} at ({x}, {SEEK_OFFSET_Y})")
                self.device.click(x, SEEK_OFFSET_Y)
                return True
            logger.info(f"{boss['name']} not visible, scroll left")
            self.swipe(self.S_BOSS_SCROLL_LEFT)
            self.device.sleep(0.8)

        return False

    # ---------------------------------------------------------------- 锁定首领
    def wait_until_spawn(self, event_time: datetime, delay: int = 3) -> None:
        """
        等到首领刷新时间 + 固定延迟
        """
        target = event_time + timedelta(seconds=delay)
        while 1:
            remain = (target - datetime.now()).total_seconds()
            if remain <= 0:
                break
            logger.attr('Wait boss spawn', f"{target.strftime('%H:%M:%S')} remain {int(remain)}s")
            wait = min(10, max(1, remain))
            self.device.sleep(wait)
            if wait > 5:
                # 长时间等待期间也要确认角色状态
                self.reset_records()
                self.screenshot()
        logger.info(f"Boss spawn time reached: {target.strftime('%H:%M:%S')}")

    def collapse_menu(self) -> None:
        """
        收起顶部菜单，避免遮挡
        """
        self.ui_close_menu()

    def get_target_name(self) -> str:
        """
        读取当前锁定目标的名称（复用 GameUi.ui_target_name）
        """
        return self.ui_target_name(scales=self.target_name_scales,
                                   min_score=self.target_name_score)

    def target_keywords(self, boss: dict) -> list:
        """
        锁定目标的名称匹配关键字：配置优先，未配置时使用首领名称
        """
        configured = self.config.world_boss.world_boss_config.target_name.strip()
        if configured:
            return [word.strip() for word in configured.split(',') if word.strip()]
        return [boss['name']]

    def lock_boss(self, boss: dict, event_time: datetime, finish_rule,
                  deadline: datetime) -> str:
        """
        等首领刷新后收起菜单，按「已锁定 -> 攻击按钮 -> 名称匹配」锁定目标

        锁定期间同时轮询当前 boss 的结束模板：如果已经被别人打死，直接返回 finished，
        不再空等目标。没有锁到目标就持续尝试到 deadline。
        :return: 'locked' / 'finished' / 'failed'
        """
        logger.hr(f"Lock {boss['name']}")
        self.wait_until_spawn(event_time)
        self.watch_finish_start(boss, finish_rule, deadline)
        keywords = self.target_keywords(boss)
        logger.attr('Target keywords', ', '.join(keywords))
        self.collapse_menu()

        # 连续锁不到目标的次数：用于降噪（只在第一次打 INFO）和「可能已被打死」提示
        no_target_rounds = 0
        no_target_hint = False
        no_target_timer = Timer(self.NO_TARGET_HINT).start()

        while datetime.now() < deadline:
            self.reset_records()
            self.screenshot()
            image = self.device.image

            if self.finish_confirmed(boss, finish_rule, image):
                logger.info(f'{boss["name"]} finish message confirmed while locking')
                return 'finished'

            if self.ui_target_locked(image=image):
                no_target_rounds = 0
                action = self.ui_target_action(image=image)
                if action == ACTION_ATTACK:
                    name = self.ui_target_name(scales=self.target_name_scales,
                                               min_score=self.target_name_score)
                    if any(self.ocr_name_match(name, word) for word in keywords):
                        logger.info(f'Target locked and matched: {name}')
                        return 'locked'
                    logger.info(f'Target [{name or "?"}] not in {keywords}, switch target')
                else:
                    logger.info(f'Target action is {action}, need attack, switch target')
            else:
                no_target_rounds += 1
                # 长时间锁不到目标时这行会每 1.3 秒刷一次，只保留第一次
                if no_target_rounds == 1:
                    logger.info('No target locked, click target button')
                else:
                    logger.debug(f'No target locked (round {no_target_rounds}), click target button')
                if not no_target_hint and no_target_timer.reached():
                    no_target_hint = True
                    logger.warning(f'No target near {boss["name"]} for {self.NO_TARGET_HINT}s, '
                                   f'it may already be finished; '
                                   f'still trying until {deadline:%H:%M:%S}')

            self.appear_then_click(self.I_TARGET_BUTTON, action=self.C_TARGET_BUTTON_CLICK)
            self.device.sleep(self.TARGET_LOCK_SWITCH_INTERVAL)

        self.screenshot()
        if self.finish_confirmed(boss, finish_rule, self.device.image):
            logger.info(f'{boss["name"]} finish message confirmed while locking')
            return 'finished'
        logger.warning(f'Target lock timeout at {deadline:%H:%M:%S}')
        return 'failed'

    def battle_task_preset(self) -> str:
        """
        本任务选的战斗预设（配置 -> battle_config.battle_preset）

        共享实现取的是任务根节点的 battle_preset，取不到嵌套在 battle_config 组里的字段，
        会静默回落到系统预设；这里覆盖，只影响世界首领任务。
        """
        return str(self.config.world_boss.battle_config.battle_preset or '')

    def finish_rule(self, boss: dict):
        """当前 boss 对应的结束模板（聊天栏 [世界]系统消息）"""
        return getattr(self, f"I_FINISH_{boss['key'].upper()}", None)

    def finish_template_score(self, finish_rule, image) -> float:
        """
        结束模板在 image 上的最高匹配分，并把 roi_front 更新到命中位置

        与 RuleImage.match 使用相同的 TM_CCOEFF_NORMED；这里单独返回分数，
        用于「0.98 以上直接确认，0.95~0.98 再做 OCR」。
        """
        source = finish_rule.corp(image)
        template = finish_rule.image
        if template is None or template.size == 0:
            return 0.0
        if template.shape[0] > source.shape[0] or template.shape[1] > source.shape[1]:
            return 0.0
        res = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        finish_rule.roi_front[0] = max_loc[0] + finish_rule.roi_back[0]
        finish_rule.roi_front[1] = max_loc[1] + finish_rule.roi_back[1]
        return float(max_val)

    def finish_confirmed(self, boss: dict, finish_rule, image) -> bool:
        """
        结束判定：模板分数为主，中间区间再用同一张截图做一次 rec-only OCR 确认

        - score >= FINISH_OCR_SKIP_SCORE(0.98)：直接判定结束，不跑 OCR；
        - threshold(0.95) <= score < 0.98：裁命中区域做单行识别，
          要求文本含 boss 名和「已被/击败」。
        :param image: 调用方传入的截图，不重新截图
        """
        if finish_rule is None:
            return False
        score = self.finish_template_score(finish_rule, image)
        if score < finish_rule.threshold:
            return False
        logger.attr('Finish template', f'{finish_rule.name} score={score:.3f}')
        if score >= self.FINISH_OCR_SKIP_SCORE:
            logger.info(f'Finish template score {score:.3f} >= {self.FINISH_OCR_SKIP_SCORE}, skip OCR')
            return True

        # 0.95~0.98：用命中位置再 OCR 一次确认，仍然复用传入的这张图
        x, y, w, h = finish_rule.roi_front
        roi = (int(x), int(y), int(w), int(h))
        ocr = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                      keyword='', name='world_boss_finish_ocr')
        ocr.cache_disable = True
        text = ocr.ocr_single_line(image)

        name_ok = self.ocr_name_match(text, boss['name'])
        kill_ok = any(word in text for word in ('已被', '击败', '被击'))
        logger.attr('Finish OCR', f'{text!r} name={name_ok} kill={kill_ok}')
        if not (name_ok and kill_ok):
            logger.warning(f'Finish template matched but OCR not confirmed: {text!r}')
            return False
        return True

    def battle_stop_reason(self, image, boss: dict, finish_rule) -> str | None:
        """
        攻击循环每帧的停止判断：结束模板+OCR 确认 -> finished；
        目标丢失或右下角不再是「攻击」-> target_lost
        """
        self.reset_records()
        if self.finish_confirmed(boss, finish_rule, image):
            logger.info(f'结束模板 {finish_rule.name} + OCR 确认，结束攻击')
            return 'finished'
        if not self.ui_target_attackable(image=image):
            logger.info('Target lost or not attackable, stop attacking')
            return 'target_lost'
        return None

    def wait_finish_template(self, boss: dict, finish_rule, timeout: float = None) -> bool:
        """目标丢失后再等一小段时间的结束模板（聊天消息可能比目标消失稍晚）"""
        if finish_rule is None:
            return False
        timeout = self.FINISH_TEMPLATE_TIMEOUT if timeout is None else timeout
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.finish_confirmed(boss, finish_rule, self.device.image):
                logger.info(f'结束模板 {finish_rule.name} + OCR 确认')
                return True
            self.device.sleep(self.FINISH_TEMPLATE_POLL)
        return False

    def check_finish(self, boss: dict, finish_rule, stage: str = '') -> bool:
        """
        用当前画面判断首领是否已经结束（复活后等没有战斗循环托管的时机调用）

        结束模板是聊天栏里的系统消息，会随新消息往上滚走。死亡复活 + 重新寻路动辄几十秒，
        这期间没人轮询，等回到 lock_boss 往往已经看不到消息了，所以复活后要立刻补查一次。

        :param stage: 调用时机（只用于日志）
        :return: 是否确认结束
        """
        if finish_rule is None:
            return False
        self._watch_muted = True
        try:
            self.screenshot()
        finally:
            self._watch_muted = False
        if self.finish_confirmed(boss, finish_rule, self.device.image):
            logger.info(f'{boss["name"]} finish confirmed'
                        f'{f" after {stage}" if stage else ""}')
            return True
        return False

    def handle_finish(self, boss: dict) -> None:
        """
        首领结束后的额外处理：2 秒内出现「首领归属奖」弹窗就点关闭，没出现直接结束

        从击杀模板/OCR 确认后开始计时；奖励弹窗已从全局弹窗迁到本任务，
        只在这里轮询，避免全局处理和其他弹窗混在一起。
        """
        logger.info(f"World boss {boss['name']} finished")
        self.watch_finish_stop()
        timer = Timer(self.FINISH_REWARD_WINDOW).start()
        clicked = False
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_BOSS_REWARD):
                x, y = self.C_BOSS_REWARD_CLOSE.coord()
                logger.info(f'Boss reward popup appeared, click close at ({x},{y})')
                self.device.click(x=x, y=y, control_name=self.C_BOSS_REWARD_CLOSE.name)
                self.device.click_record_clear()
                clicked = True
                self.device.sleep(0.4)
                continue
            if clicked:
                logger.info('Boss reward popup closed')
                return
            self.device.sleep(self.FINISH_REWARD_POLL)
        if clicked:
            logger.warning('Boss reward popup still visible after close attempts')
        else:
            logger.info(f'No boss reward popup within {self.FINISH_REWARD_WINDOW}s')

    def attack_boss(self, boss: dict, finish_rule, deadline: datetime) -> str:
        """
        锁定后按任务预设的技能队列攻击，直到目标丢失、结束模板命中或整点 deadline

        :return: 'finished' / 'target_lost' / 'timeout'
        """
        logger.hr(f"Attack {boss['name']}")
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            logger.warning(f"{boss['name']} battle deadline already reached")
            return 'timeout'

        self.screenshot()
        slots = self.battle_plan_slots()
        logger.info(f'技能顺序：{[slot + 1 for slot in slots]}')

        result = self.battle_skill_loop(
            slots,
            check_stop=lambda image: self.battle_stop_reason(image, boss, finish_rule),
            target_check=lambda image: self.ui_target_attackable(image=image),
            timeout=remaining,
            max_attempts=self.SKILL_MAX_ATTEMPTS,
            cooldown_timeout=self.SKILL_COOLDOWN_TIMEOUT,
            poll_interval=self.SKILL_COOLDOWN_POLL,
        )
        remaining = max(0.0, (deadline - datetime.now()).total_seconds())
        if result == 'target_lost':
            # 目标消失 = 首领大概率已死，但结束消息可能慢几秒才刷出来。
            # 原地等消息（不传送、不点寻）：导航类操作会把聊天消息顶走，消息一没就认不出来了。
            wait_timeout = min(self.FINISH_WAIT_AFTER_TARGET_LOST, remaining)
            if wait_timeout > 0 and self.wait_finish_template(boss, finish_rule,
                                                             timeout=wait_timeout):
                result = 'finished'
            else:
                logger.warning(f"{boss['name']} 目标消失且没等到结束消息，按已结束收尾")
                result = 'finished'
        if result == 'timeout':
            # 超时边界上结束消息可能刚出现，最后补一次模板+OCR 判断
            self.screenshot()
            if self.finish_confirmed(boss, finish_rule, self.device.image):
                logger.info(f'结束模板 {finish_rule.name} + OCR 确认')
                result = 'finished'
        if result == 'finished':
            logger.info(f"{boss['name']} battle finished")
        elif result == 'target_lost':
            logger.warning(f"{boss['name']} target lost, finish message not seen")
        else:
            logger.warning(f"{boss['name']} battle stopped: {result}")
        return result

    # ---------------------------------------------------------------- 死亡
    def handle_death(self) -> None:
        """
        角色死亡：复活后抛阶段异常，由当前事件流程捕获重试

        - 寻路/锁定阶段死亡：重新寻路到 boss；
        - 攻击阶段死亡：复活后回到 boss 处重新锁定并继续攻击。
        不走全局的 rerun_task 整任务重跑。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        stage = self._death_stage or 'navigate'
        logger.warning(f'Character died during {stage}, revive and resume')
        self.revive()
        # 等复活/地图加载稳定，加载中点击会丢
        self.device.sleep(1.0)
        raise WorldBossDeath(stage)

    # ---------------------------------------------------------------- 到达
    def wait_until_arrive(self, boss: dict, timeout: int = 180,
                          finish_rule=None) -> str:
        """
        等待自动寻路到达首领坐标

        地点与坐标统一由 GameUi.map_current_location 读取（含 OCR 形近字兼容），
        地点名用 map_name_match 比较（万剑冢 常被识别成 万剑家）。

        自动寻路中途会卡住（被障碍挡住 / 点击打断 / 寻路提前结束），此时坐标会长时间
        停在原地。这里做卡住检测：坐标连续 ARRIVE_STALL_SECONDS 秒没变化就重新点「寻」
        重启寻路（reseek_boss），避免干等到超时。

        :param finish_rule: 结束模板。传入后走位过程同步轮询「首领已结束」——
                            复活后重新寻路要几十秒，这段没有战斗循环，结束消息最容易漏
        :return: 'arrived' / 'finished' / 'timeout'
        """
        logger.hr('Wait until arrive')
        target_map = boss['target_map']
        target_coord = boss['target_coord']
        timer = Timer(timeout).start()
        stall_timer = Timer(ARRIVE_STALL_SECONDS).start()
        last_key = None

        while 1:
            self.reset_records()
            location = self.map_current_location()
            # map_current_location 已经截过一帧，直接复用这张图检查结束模板
            if finish_rule is not None and self.finish_confirmed(boss, finish_rule,
                                                                self.device.image):
                logger.info(f"{boss['name']} finish confirmed while navigating")
                return 'finished'
            if location is not None:
                map_name, coord_x, coord_y = location
                distance = abs(coord_x - target_coord[0]) + abs(coord_y - target_coord[1])
                logger.attr('Position', f'{map_name}{coord_x},{coord_y} distance {distance}')
                if self.map_name_match(map_name, target_map) and distance <= ARRIVE_TOLERANCE:
                    logger.info(f"Arrived at {map_name}{coord_x},{coord_y}")
                    return 'arrived'
                key = (map_name, coord_x, coord_y)
            else:
                # 读不到坐标：当作「没有变化」，让卡住计时器继续走
                # （真在走路时坐标会持续刷新，只有卡住/异常才会一直读不到）
                key = last_key
                if timer.reached():
                    raise GameStuckError('Unable to read character position')

            # 位置变化了 -> 重置卡住计时；连续不动到阈值 -> 重新点「寻」重启寻路
            if key != last_key:
                stall_timer.reset()
            elif stall_timer.reached():
                stall_timer.reset()
                logger.warning(f'Position stuck at {key} for {ARRIVE_STALL_SECONDS}s, reseek the boss')
                if not self.reseek_boss(boss):
                    logger.warning('Reseek failed, keep waiting')
            last_key = key

            if timer.reached():
                logger.warning(f"Arrive timeout, now at {last_key}")
                return 'timeout'
            self.device.sleep(1)

    def reseek_boss(self, boss: dict) -> bool:
        """
        重新进活动-世界首领页并点「寻」，重启自动寻路（角色卡住时用）
        """
        logger.hr(f"Reseek {boss['name']}")
        try:
            self.prepare_boss_map(boss)
            self.enter_world_boss_page()
            return self.seek_boss(boss)
        except (GameStuckError, GamePageUnknownError, GameNotRunningError) as e:
            logger.warning(f'Reseek failed: {e}')
            return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()

