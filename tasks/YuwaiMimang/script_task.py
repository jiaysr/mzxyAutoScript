# This Python file uses the following encoding: utf-8
"""
域外迷窟（YuwaiMimang）

流程：传送沼泽 -> 开小地图 -> 右侧 NPC 列表滑动找「迷窟守卫」-> 点击
     -> 人物自动走到 NPC 处 -> 等对话弹窗 -> 点「进入域外迷窟」-> 进入迷窟地图

怪物只在 10:30-11:30、15:30-16:30 刷新，其他时间可以进图但没有怪物。
"""
import math
import re
from datetime import datetime, time, timedelta

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.YuwaiMimang.assets import YuwaiMimangAssets


class ScriptTask(GameUi, YuwaiMimangAssets):
    SCHEDULER_NAME = 'YuwaiMimang'

    # 迷窟地图名（右上角地点文字）与进入后的默认坐标
    MAP_NAME = '域外迷窟'
    MAP_ENTER_POS = (306, 41)
    # 进入后校验坐标的容差（迷窟里读数偶尔有 1~2 格抖动）
    MAP_ENTER_TOLERANCE = 6

    # NPC 列表最多翻多少屏
    NPC_LIST_MAX_SWIPE = 8
    # NPC 列表滑动区域（列表内容区，不含边框）
    NPC_LIST_REGION = (975, 190, 145, 400)

    # 回基准坐标默认超时（秒），可用 config 的 recover_timeout 覆盖
    recover_timeout_default = 8

    def run(self) -> None:
        cfg = self.config.yuwai_mimang.yuwai_mimang_config
        logger.hr(f'域外迷窟 时段={cfg.monster_times} 提前={cfg.advance_time}')

        windows = self.parse_time_windows(cfg.monster_times)
        if not windows:
            logger.error(f'No valid monster time window in [{cfg.monster_times}], skip')
            self.set_next_run(task='YuwaiMimang',
                              target=self.next_prepare_time(self.default_windows(), datetime.now(),
                                                             self.advance()),
                              success=None, finish=True, server=False)
            raise TaskEnd('YuwaiMimang')

        now = datetime.now()
        slot = self.pick_slot(windows, now, self.advance())
        if slot is None:
            # 不在任何时段的准备窗口内：直接排期，把主页面让给其他任务
            self.schedule_next_run(windows)
            raise TaskEnd('YuwaiMimang')

        start, end = slot
        logger.attr('域外迷窟时段', f'{start.strftime("%H:%M")} ~ {end.strftime("%H:%M")} '
                                   f'(提前 {self.advance()} 准备)')
        # 无论后面是正常打完还是抛异常，都必须重排。
        # 本任务优先级最高，一旦 next_run 停在过去就会一直霸占 pending 第一位、
        # 把其他任务全堵死；异常路径下 task_delay 不会被调用，所以要在这里兜住。
        try:
            # 时段未开始就先准备再等；已在时段内直接开打
            self.prepare_game()
            if now < start:
                self.wait_until(start)

                # 等太久可能已经跨过时段结束（时段短或上一轮排期太晚），没怪就没必要进图
                if datetime.now() >= end:
                    logger.warning(f'Window {start:%H:%M}-{end:%H:%M} already passed while waiting, '
                                   f'skip this round')
                    # 用异常统一走 finally 的重排 + TaskEnd，避免 return 跳过收尾
                    raise TaskEnd('YuwaiMimang')
            # 时段已经过了大半就别进图了（进图流程本身要几十秒）
            remain = (end - datetime.now()).total_seconds()
            if remain < self.MIN_WINDOW_REMAIN:
                logger.warning(f'Window {start:%H:%M}-{end:%H:%M} only {int(remain)}s left, skip')
                raise TaskEnd('YuwaiMimang')

            if not self.enter_mimang(cfg):
                logger.warning('Enter mimang failed')

            # 时段内一直打怪，直到时段结束（如 11:30 / 16:30）或怪物清完
            self.handle_window(windows, slot, cfg)
        finally:
            # 先重排再抛 TaskEnd：TaskEnd 会被 script.py 捕获并 return True，
            # 而异常路径（如 GameStuckError）根本不会调 task_delay，
            # 所以重排必须放在 finally 里兜住，否则 next_run 停在过去会一直霸占调度。
            self.schedule_next_run(windows)
        raise TaskEnd('YuwaiMimang')

    # ---------------------------------------------------------------- 调度
    @staticmethod
    def default_windows() -> list:
        return [(time(10, 30), time(11, 30)), (time(15, 30), time(16, 30))]

    def parse_time_windows(self, text: str) -> list:
        """
        解析时段配置 '10:30-11:30,15:30-16:30' -> [(time(10,30), time(11,30)), ...]

        按开始时间排序；非法片段跳过并告警（全部非法返回空列表）
        """
        import re
        windows = []
        for part in (text or '').split(','):
            part = part.strip()
            if not part:
                continue
            match = re.fullmatch(r'(\d{1,2}):(\d{2})\s*[-~]\s*(\d{1,2}):(\d{2})', part)
            if not match:
                logger.warning(f'Invalid time window [{part}], expect HH:MM-HH:MM')
                continue
            sh, sm, eh, em = (int(g) for g in match.groups())
            if not (0 <= sh < 24 and 0 <= sm < 60 and 0 <= eh < 24 and 0 <= em < 60):
                logger.warning(f'Out of range time window [{part}], skip')
                continue
            start, end = time(sh, sm), time(eh, em)
            # end <= start 视为「跨零点」时段（如 23:00-01:00），保留它：
            # pick_slot 会把结束时间顺延到第二天，不会被误判成非法配置丢弃。
            # start == end 不合法（0 长度时段），直接跳过。
            if end == start:
                logger.warning(f'Time window [{part}] start equals end, skip')
                continue
            windows.append((start, end))
        windows.sort(key=lambda item: item[0])
        if windows:
            readable = ['{}-{}'.format(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in windows]
            logger.info(f'Parsed time windows: {readable}')
        return windows

    def advance(self) -> timedelta:
        advance_time = self.config.yuwai_mimang.yuwai_mimang_config.advance_time
        return timedelta(hours=advance_time.hour,
                         minutes=advance_time.minute,
                         seconds=advance_time.second)

    def pick_slot(self, windows: list, now: datetime, advance: timedelta):
        """
        找出当前应该服务的时段，返回 (start, end)；不在任何时段的准备窗口内返回 None

        命中两种情况：
        - 现在已经在时段内（start <= now < end）：直接开打
        - 现在在「开始时间 - advance」之后、时段未开始（start-advance <= now < start）：
          先准备游戏再等到 start

        支持跨零点时段（23:00-01:00）：结束时间 <= 开始时间时顺延到第二天。
        """
        for start_t, end_t in windows:
            # 跨零点时段（如 23:00-01:00）从「昨天」开始的那一段，
            # 结束时间落在今天，now 可能正处于它的后半段，所以起点要回看一天。
            cross_day = end_t <= start_t
            for start_day_offset in (1, 0) if cross_day else (0,):
                start = datetime.combine(now.date() - timedelta(days=start_day_offset), start_t)
                # 结束时间 <= 开始时间 -> 结束时间顺延到次日；
                # 否则用同一天。不能对普通时段也顺延，否则会变成
                # 「今天10:30 ~ 次日11:30」，让现在时刻永远落在第一段里。
                end_offset = 1 if end_t <= start_t else 0
                end = datetime.combine(start.date() + timedelta(days=end_offset), end_t)
                if start <= now < end:
                    return start, end
                if start - advance <= now < start:
                    return start, end
        return None

    def next_prepare_time(self, windows: list, now: datetime, advance: timedelta) -> datetime:
        """下一个时段的「开始时间 - advance」；今天的都过了就取明天第一个"""
        for day_offset in (0, 1):
            day = now.date() + timedelta(days=day_offset)
            for start_t, _ in windows:
                target = datetime.combine(day, start_t) - advance
                if target > now:
                    return target
        # 理论上到不了这里（最多两天内必然有一个未来的时段）
        return now + timedelta(days=1)

    def schedule_next_run(self, windows: list = None) -> None:
        """
        排到下一个时段的准备时间。

        server=False：不走服务端刷新时间——那会把 next_run 强行覆盖成
        「每天固定 server_update 那一刻」（见 module/config/config.py 的 task_delay），
        我们要的是精确的时段排期。
        """
        cfg = self.config.yuwai_mimang.yuwai_mimang_config
        windows = windows or self.parse_time_windows(cfg.monster_times) or self.default_windows()
        target = self.next_prepare_time(windows, datetime.now(), self.advance())
        logger.attr('下次运行', target.strftime('%Y-%m-%d %H:%M:%S'))
        self.set_next_run(task='YuwaiMimang', target=target, success=None, finish=True, server=False)

    def wait_until(self, event_time: datetime) -> None:
        """等待到指定时间（等待期间不动屏幕，只清空卡死记录）"""
        logger.hr('等待时段开始')
        while 1:
            remain = (event_time - datetime.now()).total_seconds()
            if remain <= 0:
                break
            logger.attr('域外迷窟', f'距 {event_time.strftime("%H:%M")} 还有 {int(remain)}s')
            self.device.sleep(min(2, remain))
            self.reset_records()
        logger.info(f'时段开始 {event_time.strftime("%H:%M")}')

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
            self.ui_goto_main_page()
            logger.info('游戏在线且在主页面')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError):
            logger.warning('无法切回主页面，重启游戏')
            self.ui_restart_game()

    # ---------------------------------------------------------------- 打怪
    @staticmethod
    def parse_coord(text: str):
        match = re.search(r'(\d{1,4})\s*[,，]\s*(\d{1,4})', text or '')
        if not match:
            raise GameStuckError(f'Invalid battle_coord [{text}], expect "x,y"')
        return int(match.group(1)), int(match.group(2))

    def get_target_name(self) -> str:
        """
        读取当前锁定目标的名称（复用 GameUi.ui_target_name，彩色描边字整行放大识别）
        """
        return self.ui_target_name()

    def monster_name_pick(self, target_name: str, names: list):
        """把 OCR 到的目标名匹配到已知怪物名（容忍形近字误识），匹配不上返回 None"""
        if not target_name:
            return None
        picked = self.ocr_name_pick(target_name, names)
        if picked:
            return picked
        for name in names:
            if self.ocr_name_match(target_name, name):
                return name
        return None

    def target_locked(self, names: list) -> tuple:
        """
        当前是否已锁定目标

        :return: (是否锁定, 怪物名)；没锁定时怪物名为空串
        """
        target_name = self.get_target_name()
        if not target_name:
            logger.attr('Target', '(读不到)')
            return False, ''
        name = self.monster_name_pick(target_name, names)
        logger.attr('Target', f'{target_name} -> {name or "(不匹配)"}')
        if name is None:
            return False, ''
        return True, name

    def lock_target(self, names: list, already_checked: bool = False) -> tuple:
        """
        点「目标」按钮锁定一个怪；每次只点一下然后读一次目标名

        不做成死循环去等——调用方按 max_lock_fail 计数，连续锁定不到就报错，
        这里只负责「点一次 + 判断」。

        :param already_checked: 调用方已经确认过「当前没锁目标」，
                               跳过开头那次重复的目标名读取
        :return: (是否锁定, 怪物名)
        """
        self.reset_records()
        self.screenshot()
        if not already_checked:
            # 已经锁着目标就不用再点（点「目标」会切换到下一个目标）
            locked, name = self.target_locked(names)
            if locked:
                return True, name
        if not self.appear(self.I_TARGET_BUTTON):
            logger.warning('Target button does not appear')
            return False, ''
        # 复用 GameUi 的目标锁定（点图标下方「目标」文字，比点图标稳）
        self.ui_lock_target()
        self.device.sleep(0.3)
        self.screenshot()
        return self.target_locked(names)

    # 战斗中回位的「大偏差」阈值：超过它就放弃轻推、走完整闭环 move。
    # 单次轻推最多推 0.35s（约 5~6 格），偏差在十几格以内反复轻推就能收敛；
    # 只有被怪带得很远（比如 20 格以上）才值得花几秒走一次闭环。
    RECOVER_FULL_DISTANCE = 15

    def recover_in_battle(self, coord, tolerance: int) -> bool:
        """
        战斗中的回位：小偏差轻推、大偏差走完整闭环

        为什么不用单一策略：
        - 每次都走 map_move_to（闭环）：容差 2 与单步最小位移（约 2 格）同量级，
          最后几格会反复震荡，实测一次要 8~14s，把攻击节奏全占了
        - 每次都只轻推：偏差十几格时要推很多次，且没有收敛保证

        所以按偏差大小分流；轻推返回 False（已在容差内/读不到坐标）时不做别的。
        """
        location = self.map_move_read_pos()
        if location is None:
            return False
        distance = math.hypot(location[1] - coord[0], location[2] - coord[1])
        if distance <= tolerance:
            return True
        if distance > self.RECOVER_FULL_DISTANCE:
            logger.info(f'Drifted {distance:.1f} (> {self.RECOVER_FULL_DISTANCE}), '
                        f'move back to {coord} with closed loop')
            return self.recover_position(coord, tolerance)
        return self.map_move_nudge(coord[0], coord[1], map_name=self.MAP_NAME,
                                   tolerance=tolerance)

    # 小地图右侧列表第一行「前往 <中转地图>」的点击坐标
    # 列表从上到下是：世界地图(y≈142) / 前往 沼泽(y≈222) / 前往 沼泽(y≈301) / 前往 沼泽(y≈379)
    # 这里只点第一个「前往」（y≈222），x 取按钮行中心避开左右边框
    LEAVE_ROW = (1013, 222)
    # 点「前往 沼泽」只是让角色**自动走到出口传送点**（不是瞬移），
    # 小地图很大，走过去要十几秒到几十秒
    LEAVE_TIMEOUT = 120

    def leave_mimang(self, cfg) -> bool:
        """
        离开迷窟、回中转地图（沼泽）：

        开小地图 -> 点右侧列表第一行「前往 沼泽」 -> 关小地图 -> 等角色走到出口传送

        两个坑：
        - 点「前往」只触发自动寻路，不会立刻传送，必须给它走的时间
        - 小地图开着时它盖住右上角地点文字，`map_current_location` 读不到，
          所以点完要先关掉小地图才能判断有没有真的离开
        """
        logger.hr(f'离开迷窟回 {cfg.relay_location}')
        if not self.map_open_minimap():
            logger.warning('Minimap does not open, cannot leave mimang')
            return False
        self.device.sleep(0.8)

        x, y = self.LEAVE_ROW
        logger.info(f'Click first leave entry at ({x},{y})')
        self.device.click(x=x, y=y, control_name='mimang_leave')
        self.device.click_record_clear()
        self.device.sleep(1.0)
        self.map_close_minimap()
        self.device.sleep(0.5)

        timer = Timer(self.LEAVE_TIMEOUT).start()
        while not timer.reached():
            self.reset_records()
            location = self.map_move_read_pos()
            if location is not None and not self.map_name_match(location[0], self.MAP_NAME):
                logger.info(f'Left mimang, now at [{location[0]}] {location[1]},{location[2]}')
                return True
            self.device.sleep(1)
        logger.warning(f'Still in mimang after {self.LEAVE_TIMEOUT}s')
        return False

    def read_remaining(self):
        """
        读「剩余怪物数量：N」，读不到返回 None

        用途：怪全清完时该值变 0，此时应该正常结束而不是继续锁定/报错。
        """
        results = self.O_INSIDE_REMAINING_TEXT.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(item.ocr_text for item in results)
        match = re.search(r'(\d+)', text)
        if not match:
            logger.warning(f'读不到剩余怪物数量：{text!r}')
            return None
        return int(match.group(1))

    def click_attack(self, use_skill: bool) -> None:
        """
        点一次普通攻击或技能1（复用 GameUi 的按钮区域，不重复录素材）

        两者冷却都在 1 秒以上，调用方负责间隔（cfg.attack_interval）
        """
        if use_skill:
            self.battle_click_skill(0)
        else:
            self.battle_click_attack()

    def recover_position(self, coord, tolerance: int, timeout: int = None) -> bool:
        """
        回到打怪基准坐标（被怪追踪会带着跑偏）

        攻击与移动可以同时进行（摇杆在左下、攻击键在右下），这里靠交替执行：
        攻击几轮后插一次回位。回位用 map_move_to 的闭环逻辑，
        超时/失败都不阻塞后续攻击，只记录并继续。

        :return: 是否回到基准点附近
        """
        location = self.map_move_read_pos()
        if location is None:
            return False
        distance = math.hypot(location[1] - coord[0], location[2] - coord[1])
        if distance <= tolerance:
            return True
        logger.info(f'Position drifted to {location[0]}{location[1]},{location[2]} '
                    f'(dist {distance:.1f} > {tolerance}), move back to {coord}')
        timeout = timeout if timeout is not None else self.recover_timeout_default
        # calibrate=False：迷窟与外部地图的校准值不同，且这里只是小范围回位，
        # 用 map_move_to 自带的精调即可
        return self.map_move_to(coord[0], coord[1], map_name=self.MAP_NAME,
                                tolerance=tolerance, timeout=timeout,
                                ensure_main=False, calibrate=False)

    # 每打这么多次做一次检查（读目标名，没锁到时再读剩余怪物数量）
    # 秒杀账号下攻击与锁定都是连点，逐轮 OCR 太慢；抽查即可发现「没怪了/锁不上」。
    LOCK_CHECK_EVERY = 3
    # 时段剩余时间少于这个秒数就不进图了（进图流程本身要几十秒，
    # 进去也打不了多久，还把时段尾部白白占住）
    MIN_WINDOW_REMAIN = 60

    def handle_window(self, windows: list, slot: tuple, cfg, deadline: datetime = None) -> None:
        """
        时段内打怪：反复「切换/锁定目标 -> 攻击」，一直循环到时段结束

        - 进图后先走到基准坐标（304,76），容差 2
        - 收起菜单（收起后右侧竖排才露出「目标」按钮）
        - 循环体：点「目标」锁定/切换 -> 停 attack_interval -> 点一次攻击 -> 停 -> 下一轮
        - 攻击只用普通攻击 + 技能1，两者轮换
        - **不判断单轮耗时**，一直打到时段结束（如 11:30 / 16:30）或怪物清完
        - 每 LOCK_CHECK_EVERY 次抽查一次，抽查做两件事（顺序不能反）：
          1. `recover_position` 回基准点 —— 怪会把角色追着带跑偏，
             必须在循环内回位，否则越打越远、能锁到的怪越来越少
          2. 读目标名 + 剩余数量：怪清完（剩余 0）正常收工；
             连续 max_lock_fail 次抽查都没锁到目标 -> 报错

        已知限制：快速循环每轮都会点「目标」，所以**打不死的怪会被切换掉**
        （小鬼/鬼将秒杀没问题；鬼王要两下普通攻击，靠下次再遇到它继续磨）。
        config 里的 boss_monster / boss_hits 就是为这种情况预留的，
        当前连点循环没有逐轮读目标名（读一次 ~0.3s，会把循环拖慢三倍），
        所以暂不使用；等实测确认鬼王确实磨不掉再按类型分流。

        :param deadline: 覆盖时段结束时间（调试短测用）
        """
        logger.hr('迷窟内打怪')
        names = [n.strip() for n in cfg.monster_names.split(',') if n.strip()]
        coord = self.parse_coord(cfg.battle_coord)
        tolerance = int(cfg.battle_tolerance)
        interval = float(cfg.attack_interval)
        max_fail = int(cfg.max_lock_fail)
        end = deadline if deadline is not None else slot[1]

        logger.attr('打怪配置', f'基准={coord} 容差={tolerance} 怪物={names} '
                               f'点击间隔={interval}s 最多连续失败={max_fail} '
                               f'打到={end.strftime("%H:%M")}')

        # 进图后先站到基准点
        if not self.recover_position(coord, tolerance, timeout=60):
            logger.warning(f'Cannot reach battle position {coord}, continue anyway')

        # 收起菜单：收起后右侧竖排才露出「目标」按钮
        self.ui_close_menu()
        self.device.sleep(0.5)

        try:
            self.battle_loop(names, coord, tolerance, interval, max_fail, end)
        finally:
            # 不管正常收工还是中途报错，都要离开迷窟回中转地图，
            # 别把角色留在迷窟里（报错路径更要清场，否则下次进来还得先找出口）
            try:
                self.leave_mimang(cfg)
            except Exception as e:  # noqa: BLE001
                # 清场失败不能盖掉原始异常
                logger.warning(f'Leave mimang failed: {e}')

    def battle_loop(self, names: list, coord, tolerance: int,
                    interval: float, max_fail: int, end: datetime) -> None:
        """打怪主循环：点「目标」锁定/切换 -> 点攻击，一直连点到时段结束"""
        logger.hr('开始连点打怪')
        rounds = 0
        fail = 0
        use_skill = False
        while datetime.now() < end:
            self.reset_records()

            # 点「目标」锁定/切换到下一个怪
            self.ui_lock_target()
            self.device.sleep(interval)

            # 点一次攻击（普通攻击与技能1 轮换；鬼王只用普通攻击）
            self.click_attack(use_skill=use_skill)
            use_skill = not use_skill
            rounds += 1
            self.device.sleep(interval)

            # 抽查：确认还能锁到怪、以及怪有没有清完。
            # 秒杀账号下逐轮读目标名太慢，抽查即可（连点期间本来就是盲打）。
            if rounds % self.LOCK_CHECK_EVERY:
                continue
            # 位置检查：怪会追踪把角色带着跑偏，跑偏后能锁到的怪变少，
            # 所以每次抽查都顺带回一次基准点。必须在循环内做，放到循环外等于整轮都不回位。
            # 偏差小就轻推一下（约 0.3s，不打断攻击节奏）；偏差大才走完整闭环。
            self.recover_in_battle(coord, tolerance)
            self.screenshot()
            locked, name = self.target_locked(names)
            if locked:
                fail = 0
                logger.attr('打怪进度', f'rounds={rounds} target={name}')
                continue
            # 没锁到目标：可能是怪清完了，也可能只是点偏了 -> 读剩余数量区分
            remaining = self.read_remaining()
            if remaining == 0:
                logger.info(f'剩余怪物数量为 0，本时段已清完（共 {rounds} 轮）')
                break
            fail += 1
            logger.info(f'Lock target failed ({fail}/{max_fail}), remaining={remaining}')
            if fail >= max_fail:
                raise GameStuckError(f'{max_fail} times in a row no target locked')

        logger.attr('打怪结束', f'共 {rounds} 轮')

    # ---------------------------------------------------------------- 进入
    def enter_mimang(self, cfg) -> bool:
        """主页面 -> 迷窟的完整进入流程"""
        logger.hr(f'进入域外迷窟 npc={cfg.npc_name} relay={cfg.relay_location}')
        self.ui_goto_main_page()
        location = self.map_move_read_pos()
        if location is not None and self.map_name_match(location[0], self.MAP_NAME):
            logger.info(f'Already in [{self.MAP_NAME}], skip entering')
            return True

        # 只在不在中转地图时才传送：世界地图列表里不包含「当前所在地点」，
        # 已经在沼泽时再调 map_teleport('沼泽') 必然找不到目标（map.py 的已知行为）
        in_relay = location is not None and self.map_name_match(location[0], cfg.relay_location)
        if in_relay:
            logger.attr('Relay location', f'Already at [{location[0]}] {location[1]},{location[2]}, skip teleport')
        elif not self.map_teleport(cfg.relay_location):
            raise GameStuckError(f'Cannot teleport to relay location [{cfg.relay_location}]')

        # 点 NPC 后人物要走过去，路远或画面抖动都可能错过弹窗；重试一次
        # （实测失败过一次：NPC 列表第 3 屏找到并点了，但 60s 内弹窗没出来）
        for attempt in (1, 2):
            if self.open_minimap_and_click_npc(cfg.npc_name, cfg.dialog_timeout):
                if self.click_enter_mimang(cfg.enter_text, cfg.enter_wait):
                    self.verify_inside()
                    logger.info('Now in yuwei mimang')
                    return True
                logger.warning(f'Enter option failed (attempt {attempt})')
            else:
                logger.warning(f'NPC dialog does not appear (attempt {attempt})')
            # 重试前清掉可能残留的弹窗/小地图，避免它们盖住入口
            self.close_leftover_dialog()
            self.map_close_minimap()
            self.device.sleep(0.5)

        raise GameStuckError('Cannot enter yuwei mimang after 2 attempts')

    # ---------------------------------------------------------------- 进入
    def ui_goto_main_page(self) -> bool:
        """确保在主页面（只有主页面有小地图入口）"""
        from tasks.GameUi.page import page_main
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=60):
            raise GameStuckError('Main page does not appear')
        self.close_leftover_dialog()
        self.map_close_main_popup()
        return True

    def close_leftover_dialog(self, timeout: int = 5) -> None:
        """
        关掉上一次运行可能残留的 NPC 对话弹窗

        必须做：弹窗会盖住右上角小地图入口，导致 map_open_minimap 一直点不开
        （实测连点6 次入口都无效，log 显示 Minimap does not appear）。
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_NPC_DIALOG_X):
                return
            logger.info('Close leftover NPC dialog')
            self.appear_then_click(self.I_NPC_DIALOG_X, action=self.C_DIALOG_CLOSE, interval=1)
            self.device.click_record_clear()
        logger.warning('Leftover NPC dialog does not close')

    def open_minimap_and_click_npc(self, npc_name: str, dialog_timeout: int = 60) -> bool:
        """
        开小地图 -> 右侧 NPC 列表滑动找 npc_name -> 点击 -> 等对话弹窗

        人物会自己走到 NPC 处，所以点完 NPC 只要等弹窗出现，不用管移动过程。

        :param npc_name: NPC 名（如 迷窟守卫）
        :param dialog_timeout: 等对话弹窗的超时（秒）
        """
        logger.hr(f'Find NPC [{npc_name}] in minimap')
        # 先关掉残留的 NPC 对话弹窗：它盖住小地图右上角的「世界地图」按钮，
        # 会让 map_open_minimap 的识别一直失败（连点入口也没用，实测踩过）
        self.close_leftover_dialog()
        self.map_close_minimap()
        self.device.sleep(0.3)
        if not self.map_open_minimap():
            logger.error('Minimap does not open')
            return False

        try:
            if not self.minimap_find_npc_click(npc_name):
                return False
        finally:
            # 点完 NPC 后人物自动寻路，小地图会自己关；这里先主动关掉，
            # 否则小地图会挡住地图画面，寻路途中的坐标读取/NPC 出现都看不到。
            # 关不掉也无所谓——NPC 弹窗出现时会盖住小地图。
            self.map_close_minimap()

        return self.wait_npc_dialog(dialog_timeout)

    def minimap_find_npc_click(self, npc_name: str) -> bool:
        """
        在小地图右侧 NPC 列表里找 npc_name 并点击（列表可上下滚动）

        用 detect_and_ocr 拿全部文本框自己做包含判断，不用 ocr_appear：
        框架的 Full 模式在整串不匹配时会退化成逐字符匹配，几乎总返回 True。
        """
        x0, y0, w, h = self.NPC_LIST_REGION
        last_signature = None
        for attempt in range(1, self.NPC_LIST_MAX_SWIPE + 1):
            self.reset_records()
            self.screenshot()
            coord = self.minimap_npc_locate(npc_name)
            if coord is not None:
                logger.attr(f'NPC [{npc_name}] found at {attempt}th scan', f'{coord[0]:.0f},{coord[1]:.0f}')
                self.device.click(x=coord[0], y=coord[1], control_name=f'mimang_npc_{npc_name}')
                self.device.click_record_clear()
                return True
            # 列表内容没变 = 已经到底
            signature = self.minimap_npc_signature()
            if signature == last_signature:
                logger.warning(f'NPC list does not scroll any more, [{npc_name}] not found')
                return False
            last_signature = signature
            logger.info(f'NPC [{npc_name}] not on screen {attempt}, scroll list down')
            self.minimap_npc_swipe_down()
        logger.error(f'NPC [{npc_name}] not found after {self.NPC_LIST_MAX_SWIPE} scrolls')
        return False

    def minimap_npc_locate(self, npc_name: str):
        """
        在 NPC 列表区域 OCR 找 npc_name，返回可点击坐标；没找到返回 None

        命中判定用「OCR 文本包含 npc_name 的不易误识片段」：
        OCR 会把形近字认错（「迷」->「迷」还好，但「窟」「卫」都可能出错），
        所以同时接受前两字一致 + 相似度达标。
        """
        rule = self.O_MINIMAP_NPC_TEXT
        old_roi = rule.roi
        try:
            rule.roi = self.NPC_LIST_REGION
            results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        finally:
            rule.roi = old_roi
        texts = [item.ocr_text.strip() for item in results]
        logger.info(f'NPC list texts: {texts}')
        for item in results:
            text = item.ocr_text.strip()
            if not text:
                continue
            if not (self.ocr_name_match(text, npc_name) or self.ocr_name_pick(text, [npc_name])):
                continue
            xs = [point[0] for point in item.box]
            ys = [point[1] for point in item.box]
            roi = self.NPC_LIST_REGION
            x = (min(xs) + max(xs)) / 2 + roi[0]
            y = (min(ys) + max(ys)) / 2 + roi[1]
            return int(x), int(y)
        return None

    def minimap_npc_signature(self) -> tuple:
        """当前列表可见文本的签名，用来判断滑动是否还有效（到底/卡住）"""
        rule = self.O_MINIMAP_NPC_TEXT
        old_roi = rule.roi
        try:
            rule.roi = self.NPC_LIST_REGION
            results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        finally:
            rule.roi = old_roi
        return tuple(sorted(item.ocr_text.strip() for item in results))

    def minimap_npc_swipe_down(self) -> None:
        """NPC 列表往下滚一屏（minitouch 滑动很快，拆成多段小滑动防止甩过头）"""
        x0, y0, w, h = self.NPC_LIST_REGION
        center_x = x0 + w // 2
        top, bottom = y0 + 30, y0 + h - 30
        logger.info(f'Scroll NPC list: ({center_x},{bottom}) -> ({center_x},{top})')
        self.ui_swipe_gentle((center_x, bottom), (center_x, top), steps=3, step_delay=0.2)
        self.device.click_record_clear()
        self.device.sleep(0.8)

    def wait_npc_dialog(self, timeout: int = 60) -> bool:
        """
        等 NPC 对话弹窗出现（人物在自动走到 NPC 处，这期间画面会滚动）
        """
        logger.hr(f'Wait NPC dialog (timeout {timeout}s)')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_NPC_DIALOG_X):
                # 确认标题是目标 NPC，避免点错别的 NPC
                if self.appear(self.I_NPC_DIALOG_TITLE):
                    logger.info('NPC dialog appeared')
                    return True
                logger.warning('Dialog appeared but title does not match, keep waiting')
            self.device.sleep(0.5)
        logger.warning('NPC dialog does not appear')
        return False

    def click_enter_mimang(self, enter_text: str, enter_wait: int = 2) -> bool:
        """点「进入域外迷窟」选项，随后等地图加载"""
        logger.hr(f'Click [{enter_text}]')
        # 先用文字素材确认「进入域外迷窟」这一行存在，再点整行
        if not self.appear(self.I_NPC_DIALOG_ENTER_TEXT):
            logger.warning(f'Enter option [{enter_text}] does not appear')
            return False
        x, y = self.C_NPC_DIALOG_ENTER.coord()
        logger.info(f'Click enter option at ({x},{y})')
        self.device.click(x=x, y=y, control_name='mimang_enter')
        self.device.click_record_clear()
        self.device.sleep(max(1, enter_wait))
        return True

    def verify_inside(self) -> None:
        """确认已进入域外迷窟（校验地点名 + 默认坐标）"""
        logger.hr('Verify entering yuwei mimang')
        timer = Timer(30).start()
        last = None
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            location = self.map_current_location()
            if location is None:
                self.device.sleep(0.5)
                continue
            last = location
            if not self.map_name_match(location[0], self.MAP_NAME):
                logger.info(f'Not in [{self.MAP_NAME}] yet (now [{location[0]}]), wait')
                self.device.sleep(0.5)
                continue
            distance = ((location[1] - self.MAP_ENTER_POS[0]) ** 2
                        + (location[2] - self.MAP_ENTER_POS[1]) ** 2) ** 0.5
            logger.attr('Entered position', f'{location[1]},{location[2]} '
                                           f'(default {self.MAP_ENTER_POS[0]},{self.MAP_ENTER_POS[1]}, '
                                           f'dist {distance:.1f})')
            if distance <= self.MAP_ENTER_TOLERANCE:
                logger.info('Enter position matches')
                return
            logger.info('In map but position differs, waiting for loading to settle')
            self.device.sleep(0.5)
        raise GameStuckError(f'Still not in [{self.MAP_NAME}] (last {last})')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass