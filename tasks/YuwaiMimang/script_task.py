# This Python file uses the following encoding: utf-8
"""
域外迷窟（YuwaiMimang）

流程：传送沼泽 -> 开小地图 -> 右侧 NPC 列表滑动找「迷窟守卫」-> 点击
     -> 人物自动走到 NPC 处 -> 等对话弹窗 -> OCR 选项区找「进入域外迷窟」
     （列表可滚动，找不到会自动上下翻）-> 点文字位置 -> 进入迷窟地图
     -> 锁白名单目标 -> 打怪 -> 时段结束（11:30 / 16:30）后离场

打怪（配置 -> 战斗配置）：
- 进图后识别 7 个技能位分别是什么技能，按任务选的战斗预设排队释放
  （双击 -> 校验进冷却 -> 冷却好的高优先级技能立刻插队）；
  默认不点普通攻击（任务「战斗配置」里可以打开），技能全冷却时等冷却；
- 只打「战斗目标」白名单（默认 小鬼,鬼王,鬼将）里的怪：锁目标时逐个切换并
  OCR 名字校验，列表外的怪（例如路过的玩家）不碰（见 acquire_target）；
- 一刀一只时目标死了就地快速补锁（`fast_relock`，默认开），单只重锁 ~1.1s -> ~0.3s。

怪物在 10:30-11:30、15:30-16:30 两个时段内**一直刷新**（不会打完），
所以打怪不判「清完了」，只在时段到点（11:30 / 16:30）时收工离场。
其他时间可以进图但没有怪物。

场次与让路：
- `join_first_window` / `join_second_window` 决定参加 `monster_times` 里的第 1、2 场；
- 上午 11:00 世界首领：本任务勾了 `join_world_boss`、且世界首领任务启用了并勾了
  11 点那场时，本场提前 3 分钟（10:57）收工离场，离场后再排到 11:00 + back 分钟回场
  （见 world_boss_yield_times / schedule_after_session）。
"""
import logging
import math
import re
from contextlib import contextmanager
from datetime import datetime, time, timedelta

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.Battle.target_action import ACTION_ATTACK
from tasks.GameUi.game_ui import GameUi
from tasks.YuwaiMimang.assets import YuwaiMimangAssets


class _SuppressControlInputLog(logging.Filter):
    """
    战斗连点期间屏蔽底层输入事件日志（control.py 的 Click / Hold drag 等 INFO）

    连点每秒会产生好几条点击日志，会把进度、回位、修理这些真正有用的信息淹没；
    输入事件本身没有阅读价值（排查问题看的是移动/回位/死亡等其他模块的日志）。
    WARNING 及以上不过滤。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.filename == 'control.py'
                    and record.levelno < logging.WARNING)


@contextmanager
def suppress_control_input_log():
    """屏蔽 control.py 的 INFO 日志，退出上下文时自动恢复"""
    filt = _SuppressControlInputLog()
    logger.addFilter(filt)
    try:
        yield
    finally:
        logger.removeFilter(filt)


class ScriptTask(GameUi, YuwaiMimangAssets):
    SCHEDULER_NAME = 'YuwaiMimang'

    # 迷窟地图名（右上角地点文字）与进入后的默认坐标
    MAP_NAME = '域外迷窟'
    MAP_ENTER_POS = (306, 41)
    # 进入后校验坐标的容差（迷窟里读数偶尔有 1~2 格抖动）
    MAP_ENTER_TOLERANCE = 6

    # NPC 列表最多翻多少屏（每次 2 行，到底会自动停）
    NPC_LIST_MAX_SWIPE = 12
    # NPC 列表滑动区域（列表内容区，不含边框）
    NPC_LIST_REGION = (975, 190, 145, 400)
    # 列表行距 = NPC 区域 68px + 间隙 10px（真机实测），每次滚 2 行 = 156px
    NPC_LIST_ROW_PITCH = 78
    NPC_LIST_SWIPE_ROWS = 2
    # 拖动到底后按住的时间：抵消松手惯性（快速滑动一滑即抬，实测拖 240px 会甩出整整一屏）
    NPC_LIST_HOLD = 0.4
    # 滚完等列表停稳的固定等待（实测惯性 0.6~0.9s 内停住）
    NPC_LIST_SETTLE_SLEEP = 1.0

    # 回基准坐标默认超时（秒），可用 config 的 recover_timeout 覆盖
    recover_timeout_default = 8

    def run(self) -> None:
        cfg = self.config.yuwai_mimang.yuwai_mimang_config
        logger.hr(f'域外迷窟 时段={cfg.monster_times} 提前={cfg.advance_time}')
        self._world_boss_resume = None

        windows = self.parse_time_windows(cfg.monster_times)
        if not windows:
            logger.error(f'No valid monster time window in [{cfg.monster_times}], skip')
            self.set_next_run(task='YuwaiMimang',
                              target=self.next_prepare_time(self.default_windows(), datetime.now(),
                                                             self.advance()),
                              success=None, finish=True, server=False)
            raise TaskEnd('YuwaiMimang')

        # 只保留勾选的场次（默认两场都参加）
        selected = self.selected_windows(windows, cfg)
        if not selected:
            logger.warning('没有勾选任何参与场次（join_first_window / join_second_window），跳过')
            self.schedule_next_run(windows)
            raise TaskEnd('YuwaiMimang')

        now = datetime.now()
        slot = self.pick_slot(selected, now, self.advance())
        if slot is None:
            # 不在任何时段的准备窗口内：直接排期，把主页面让给其他任务
            self.schedule_next_run(selected)
            raise TaskEnd('YuwaiMimang')

        start, end = slot
        logger.attr('域外迷窟时段', f'{start.strftime("%H:%M")} ~ {end.strftime("%H:%M")} '
                                   f'(提前 {self.advance()} 准备)')

        # 11:00 的世界首领：本任务和世界首领两边都勾了，才让路（提前收工离场）
        boss_at, leave_at, resume_at = self.world_boss_yield_times(slot, cfg)
        effective_end = end
        if leave_at is not None and now < leave_at:
            effective_end = leave_at
            logger.attr('世界首领让路', f'{leave_at:%H:%M} 收工离场（首领 {boss_at:%H:%M}）')
            self._world_boss_resume = resume_at
        elif boss_at is not None and now < boss_at:
            # 已经过了让路时间、首领还没刷：这场别进了，等首领结束再回场
            logger.info(f'已过让路时间 {leave_at:%H:%M}（首领 {boss_at:%H:%M}），本场不进图')
            self._world_boss_resume = resume_at
            self.schedule_after_session(selected)
            raise TaskEnd('YuwaiMimang')

        # 无论后面是正常打完还是抛异常，都必须重排。
        # 本任务优先级最高，一旦 next_run 停在过去就会一直霸占 pending 第一位、
        # 把其他任务全堵死；异常路径下 task_delay 不会被调用，所以要在这里兜住。
        try:
            # 时段未开始就先准备再等；已在时段内直接开打
            self.prepare_game()
            if now < start:
                self.wait_until(start)

                # 等太久可能已经跨过时段结束（时段短或上一轮排期太晚），没怪就没必要进图
                if datetime.now() >= effective_end:
                    logger.warning(f'Window {start:%H:%M}-{end:%H:%M} already passed while waiting, '
                                   f'skip this round')
                    # 用异常统一走 finally 的重排 + TaskEnd，避免 return 跳过收尾
                    raise TaskEnd('YuwaiMimang')
            # 时段已经过了大半就别进图了（进图流程本身要几十秒）
            remain = (effective_end - datetime.now()).total_seconds()
            if remain < self.MIN_WINDOW_REMAIN:
                cut = '' if effective_end == end else f'，让路到 {effective_end:%H:%M}'
                logger.warning(f'Window {start:%H:%M}-{end:%H:%M} only {int(remain)}s left{cut}, skip')
                raise TaskEnd('YuwaiMimang')

            if not self.enter_mimang(cfg):
                logger.warning('Enter mimang failed')

            # 时段内一直打怪，直到本场结束（让路时提前到 10:57）
            self.handle_window(selected, slot, cfg, deadline=effective_end)
        finally:
            # 先重排再抛 TaskEnd：TaskEnd 会被 script.py 捕获并 return True，
            # 而异常路径（如 GameStuckError）根本不会调 task_delay，
            # 所以重排必须放在 finally 里兜住，否则 next_run 停在过去会一直霸占调度。
            self.schedule_after_session(selected)
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

    # ---------------------------------------------------------------- 参与场次 / 世界首领
    # 上午 11:00 刷新的世界首领：本任务在时段内一直霸占调度，必须提前收工让路
    WORLD_BOSS_TIME = time(11, 0)
    # 提前这么多分钟离场（离场流程本身要几十秒，走太晚世界首领任务就过期了）
    WORLD_BOSS_LEAD_MINUTES = 3
    # 因世界首领让路时的回场时间（run 收尾排期用）
    _world_boss_resume = None

    def selected_windows(self, windows: list, cfg) -> list:
        """
        只保留勾选的参与场次（join_first_window / join_second_window）

        场次按 monster_times 解析后的顺序（已按开始时间排序）对应：
        第 1 场 = 第一个时段（默认 10:30-11:30），第 2 场 = 第二个（默认 15:30-16:30）。
        配了两个以上时段时，第 3 场及之后视为参加（只给前两场提供开关）。
        """
        flags = (bool(getattr(cfg, 'join_first_window', True)),
                 bool(getattr(cfg, 'join_second_window', True)))
        selected = [window for index, window in enumerate(windows)
                    if index >= len(flags) or flags[index]]
        if len(selected) != len(windows):
            readable = ['{}-{}'.format(s.strftime('%H:%M'), e.strftime('%H:%M'))
                        for s, e in selected]
            logger.attr('参与场次', readable or 'none')
        return selected

    def world_boss_at(self, hour: int) -> bool:
        """
        世界首领任务是否勾选了 hour 点的首领（且任务本身是启用的）

        迷窟要不要让路，除了自己的开关，还得看世界首领那边到底打不打：
        任务没启用、或者没勾这一场，就没必要白让出半小时。
        """
        wb = getattr(getattr(self.config, 'model', None), 'world_boss', None)
        if wb is None or not getattr(getattr(wb, 'scheduler', None), 'enable', False):
            return False
        field = f'time_{int(hour):02d}'
        for group_name in ('manchui', 'wuqing', 'julian', 'fuwang', 'xiewang'):
            group = getattr(wb, group_name, None)
            if group is not None and getattr(group, field, False):
                logger.attr('世界首领', f'{group_name} {int(hour):02d}:00 已勾选，到点让路')
                return True
        return False

    def world_boss_yield_times(self, slot, cfg):
        """
        本场是否需要给 11:00 的世界首领让路

        三个条件都要满足：本任务勾了 join_world_boss、
        世界首领任务启用了且勾了 11 点那场、11:00 落在本时段内。

        :return: (boss_at, leave_at, resume_at)；不让路时三个都是 None。
                 leave_at = 首领时间 - WORLD_BOSS_LEAD_MINUTES（提前离场走流程）；
                 resume_at = 首领时间本身 —— 到点后本任务重新进入 pending，
                 世界首领任务还在跑就继续等，它一结束（自带 10 分钟超时）
                 调度器按正常优先级把它排上，所以不需要额外的「回场延迟」
        """
        if not getattr(cfg, 'join_world_boss', False):
            return None, None, None
        start, end = slot
        boss_at = datetime.combine(start.date(), self.WORLD_BOSS_TIME)
        if not (start <= boss_at < end):
            return None, None, None
        if not self.world_boss_at(boss_at.hour):
            logger.info(f'{boss_at:%H:%M} 的世界首领没勾选（或任务未启用），本场不让路')
            return None, None, None
        leave_at = boss_at - timedelta(minutes=self.WORLD_BOSS_LEAD_MINUTES)
        resume_at = boss_at if boss_at < end else None
        return boss_at, leave_at, resume_at

    def schedule_after_session(self, windows: list) -> None:
        """
        收尾排期：因世界首领让路收工 -> 排到首领时间（之后按正常优先级排队）；
        其余情况按原逻辑排下一个时段的准备时间。
        """
        resume = self._world_boss_resume
        self._world_boss_resume = None
        if resume is not None and resume > datetime.now():
            logger.attr('下次运行', f'{resume:%Y-%m-%d %H:%M:%S}（到首领时间重新排队）')
            self.set_next_run(task='YuwaiMimang', target=resume, success=None,
                              finish=True, server=False)
            return
        self.schedule_next_run(windows)

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

    # 迷窟小地图上的三个出口：(x, y, w, h, 先拖到哪个角落)
    # 坐标是实测的图标区域（1280x720），三个出口分布在两张不同的视图里：
    #   1 号 = 地图右上角那个出口（拖到 top_right 后可点）
    #   2 号 = 左侧中部（同一张 top_right 视图里）
    #   3 号 = 地图左下角（要拖到 bottom_right/bottom_left 才露出来）
    # 依据：两张小地图截图 —— 右上视图只有底边箭头，底部视图只有顶边箭头，
    # 3 号图标只在底部视图里出现（y≈590），右上视图同一位置是空地。
    # 三个出口不都能用（有的通往别处/当前关闭），所以按顺序逐个试
    LEAVE_EXITS = (
        (704, 134, 31, 32, 'top_right'),
        (233, 411, 31, 32, 'top_right'),
        (247, 571, 31, 32, 'bottom_right'),
    )
    # 每个出口给多久走到并传送出去（点出口只触发自动寻路，不是瞬移）
    LEAVE_WAIT = 60

    def leave_mimang(self, cfg) -> bool:
        """
        离开迷窟、回中转地图（沼泽）：依次点小地图上的三个出口，直到出图

        每个出口一轮（最多 3 轮）：
        开小地图 -> `map_pan_to_corner(角落)` 把地图拖到该出口所在的角落
        -> 点该出口 -> 关小地图 -> 等角色自动走到出口传送（最多 LEAVE_WAIT 秒）
        -> 还在迷窟就换下一个出口。

        为什么按顺序试三个：迷窟小地图上有三个出口，个别出口可能当前不可用
        （关闭 / 通往别的地图），点一次没出去就换下一个，比死守一个稳。

        为什么每个出口要拖到不同的角落：出口分布在地图的上下两端，
        一张视图（地图窗口 785x525）装不下三个，坐标是在各自视图里量的；
        先拖到角落视图才固定（否则视口跟着角色走，坐标会漂）。

        两个坑（沿用旧实现的经验）：
        - 点出口只触发自动寻路，不会立刻传送，必须给它走的时间；
        - 小地图开着时它盖住右上角地点文字，`map_current_location` 读不到，
          所以点完要先关掉小地图才能判断有没有真的离开。
        """
        logger.hr(f'离开迷窟回 {cfg.relay_location}')
        for index, (x, y, w, h, corner) in enumerate(self.LEAVE_EXITS, start=1):
            # 上一轮可能已经出去了（但坐标没读到）：先确认一下，别在主页面瞎点
            location = self.map_current_location()
            if location is not None and not self.map_name_match(location[0], self.MAP_NAME):
                logger.info(f'Already left mimang, now at [{location[0]}]')
                return True

            if not self.map_open_minimap():
                logger.warning('Minimap does not open, cannot leave mimang')
                return False
            self.device.sleep(0.8)

            if not self.map_pan_to_corner(corner):
                logger.warning(f'Pan minimap to [{corner}] failed, click the exit anyway')
            self.device.sleep(0.3)

            px, py = int(x + w / 2), int(y + h / 2)
            logger.info(f'Click mimang exit #{index}/{len(self.LEAVE_EXITS)} at ({px},{py}) '
                        f'[{corner}]')
            self.device.click(x=px, y=py, control_name=f'mimang_leave_{index}')
            self.device.click_record_clear()
            self.device.sleep(1.0)
            self.map_close_minimap()
            self.device.sleep(0.5)

            if self.wait_leave_mimang():
                return True
            logger.warning(f'Exit #{index} does not work within {self.LEAVE_WAIT}s, '
                           f'try the next one')

        logger.warning(f'Still in mimang after all {len(self.LEAVE_EXITS)} exits')
        return False

    def wait_leave_mimang(self, timeout: float = None) -> bool:
        """
        等角色走到出口、真的离开迷窟（右上角地点名不再是「域外迷窟」）

        :param timeout: 等待秒数，默认 LEAVE_WAIT（60）
        :return: 已离开返回 True；超时仍在地图里返回 False
        """
        timeout = self.LEAVE_WAIT if timeout is None else float(timeout)
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            location = self.map_move_read_pos()
            if location is not None and not self.map_name_match(location[0], self.MAP_NAME):
                logger.info(f'Left mimang, now at [{location[0]}] {location[1]},{location[2]}')
                return True
            self.device.sleep(1)
        logger.warning(f'Still in mimang after {timeout:.0f}s')
        return False

    # ---------------------------------------------------------------- 战斗配置 / 目标
    def yuwai_battle_config(self):
        """本任务的「战斗配置」组（预设 / 普通攻击 / 快速补锁 / 战斗目标）"""
        return self.config.yuwai_mimang.battle_config

    def battle_task_preset(self) -> str:
        """
        本任务选的战斗预设（配置 -> 战斗配置 -> 战斗预设）

        共享实现现在会读 `battle_config` 组；这里仍然自己取，是为了直跑脚本（没有任务上下文）时也能拿到，
        取不到嵌套在 `battle_config` 组里的字段，会静默回落到系统预设；
        世界首领 / 仙府九重天也是各自覆盖的，这里只影响域外迷窟。
        """
        return str(self.yuwai_battle_config().battle_preset or '')

    def battle_target_names(self) -> list:
        """
        要打的目标名白名单（配置 -> 战斗配置 -> 战斗目标）

        逗号、中文逗号、分号、空格都算分隔符；空列表 = 不校验目标名
        """
        text = str(getattr(self.yuwai_battle_config(), 'target_names', '') or '')
        return [name for name in re.split(r'[,，;；\s]+', text) if name]

    # 锁目标：最多点几次「目标」按钮 / 整体超时（秒）
    # 迷窟里怪多，切几次就能命中；等太久会把打怪节奏拖没
    TARGET_SWITCH_MAX = 8
    TARGET_SWITCH_TIMEOUT = 8
    # 连续这么多次锁不到白名单目标：告警一次（不要退回「打任意目标」——
    # 迷窟里有玩家，名字对不上时宁可继续切，也不能打错人）
    TARGET_WARN_AFTER = 10
    _target_fail_count = 0

    def acquire_target(self, names: list = None) -> bool:
        """
        锁一个「可攻击 + 名字在白名单里」的目标（完整流程）

        靠 GameUi 的 `ui_ensure_target_locked`：收起菜单 -> 判断是否已锁定且动作按钮为「攻击」
        -> 不满足就点「目标」切换 -> 命中白名单名字才返回。

        它一次约 1.1s，开销几乎都不在 OCR 上（名字 OCR 只有 ~20ms）：
        `ui_close_menu`、通用换目标间隔 0.5s、以及每次判定各截一次图。
        所以每杀一只走这条路太贵，常规补锁走下面的 `relock_target`，
        这里只在开打、补锁失败、或需要从菜单/死亡状态里恢复时用。

        连续 `TARGET_WARN_AFTER` 次都锁不到只告警、继续尝试：
        **绝不退回「打任意可攻击目标」**，避免锁到路过的敌对玩家。

        :param names: 白名单（None = 读配置）；传空列表 / 配置为空 = 不校验名字
        :return: 锁到目标返回 True；超时或切满了还没命中返回 False
        """
        names = self.battle_target_names() if names is None else names
        if self.ui_ensure_target_locked(
                target_name=names or None,
                action=ACTION_ATTACK,
                max_switch=self.TARGET_SWITCH_MAX,
                timeout=self.TARGET_SWITCH_TIMEOUT):
            self._target_fail_count = 0
            return True

        self._target_fail_count += 1
        if self._target_fail_count % self.TARGET_WARN_AFTER == 1:
            logger.warning(f'连续 {self._target_fail_count} 次没锁到白名单目标 {names}，'
                           f'继续等待（不会打白名单外的目标）')
        return False

    def relock_target(self, names: list = None) -> bool:
        """
        目标死后快速补锁（实现见 SimpleBattle.battle_relock_target）

        迷窟属于「一个坐标周围持续刷怪、刷新间隔约 3s、要求在时段内尽量多杀」的场景：
        一刀一只时每只都要重锁，完整重锁 ~1.1s、补锁 ~0.3s，所以默认开启
        （配置 -> 战斗配置 -> fast_relock）。关掉后退回正常流程：
        目标丢失 -> 结束本次释放循环 -> 外层用完整 acquire_target 重锁。
        """
        names = self.battle_target_names() if names is None else names
        return self.battle_relock_target(names)

    def battle_target_ok(self, names: list, image=None) -> bool:
        """
        放技能前确认「锁着的就是白名单目标」，避免打到路过的玩家。

        每次都现查名字（不缓存）：玩家可攻击，只判「可攻击」会在怪死后 /
        游戏自动转锁到玩家时继续打偏（补锁只在目标不可攻击时才触发，玩家可攻击
        就永远不补锁）。名字读不出来时按原目标处理，别因一次 OCR 抖动就停手。
        白名单为空 = 配置不校验名字，只要能攻击就行。
        """
        if not self.ui_target_attackable(image=image):
            return False
        if not names:
            return True
        name = self.ui_target_name()
        if not name:
            return True
        if any(self.ocr_name_match(name, expected) for expected in names):
            return True
        logger.warning(f'锁着的目标变成 [{name}]，不在白名单 {names}，停止放技能')
        return False

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

    # 时段剩余时间少于这个秒数就不进图了（进图流程本身要几十秒，
    # 进去也打不了多久，还把时段尾部白白占住）
    MIN_WINDOW_REMAIN = 60

    # 每成功释放这么多次技能做一次位置检查 / 中途修理
    # （一次释放 = 一次「双击 + 等进冷却」）
    RECOVER_EVERY_CASTS = 6
    # 预设模式：所有技能都在冷却时，普攻的最小间隔（秒）
    NORMAL_ATTACK_INTERVAL = 1.0
    # 预设模式：每个技能的释放节奏（重试次数 / 冷却超时）统一走「战斗预设」，这里只留轮询间隔
    SKILL_COOLDOWN_POLL = 0.1
    # 清怪检查已移除：怪物在整个时段内会一直刷新，「剩余怪物数量」读到 0 只是
    # 当下的瞬间（马上又刷），所以一律打满时段（11:30 / 16:30 到点才收工）。
    # 本场打怪的截止时间（battle_stop_reason 用；由 reset_battle_progress 设置）
    _battle_deadline = None

    def handle_window(self, windows: list, slot: tuple, cfg, deadline: datetime = None) -> None:
        """
        时段内打怪：锁白名单目标 -> 按预设释放技能 -> 打完换下一只，直到时段到点
        （11:30 / 16:30；怪物在时段内一直刷新，所以不判「清完了」）

        - 进图后先走到基准坐标（304,76），容差 2
        - 收起菜单（收起后右侧竖排才露出「目标」按钮）
        - 只打「战斗目标」白名单（默认 小鬼,鬼王,鬼将）里的怪（见 acquire_target）
        - 每打若干次回一次基准坐标（见 recover_in_battle），并按 repair_interval_minutes
          中途修理装备（时段剩余不足 REPAIR_MIN_REMAIN 秒时跳过）

        :param deadline: 覆盖时段结束时间（调试短测用）
        """
        logger.hr('迷窟内打怪')
        coord = self.parse_coord(cfg.battle_coord)
        tolerance = int(cfg.battle_tolerance)
        end = deadline if deadline is not None else slot[1]
        names = self.battle_target_names()

        logger.attr('打怪配置', f'目标={names or "不限"} 基准={coord} '
                               f'容差={tolerance} 打到={end.strftime("%H:%M")}')

        # 进图后先站到基准点
        if not self.recover_position(coord, tolerance, timeout=60):
            logger.warning(f'Cannot reach battle position {coord}, continue anyway')

        # 收起菜单：收起后右侧竖排才露出「目标」按钮
        self.ui_close_menu()
        self.device.sleep(0.5)

        try:
            self.preset_battle_loop(coord, tolerance, end, names, cfg)
        finally:
            # 不管正常收工还是中途报错，都要离开迷窟回中转地图，
            # 别把角色留在迷窟里（报错路径更要清场，否则下次进来还得先找出口）
            try:
                self.leave_mimang(cfg)
            except Exception as e:  # noqa: BLE001
                # 清场失败不能盖掉原始异常
                logger.warning(f'Leave mimang failed: {e}')

    # ---------------------------------------------------------------- 打怪：按预设
    def reset_battle_progress(self, end: datetime) -> None:
        """重置本场打怪的进度状态（时段截止时间）"""
        self._battle_deadline = end

    # 技能位识别：连试几次（刚进战斗时技能栏可能还在动画 / 被特效挡住）
    SLOT_RECOGNIZE_ATTEMPTS = 3
    SLOT_RECOGNIZE_WAIT = 1.0

    def recognize_skill_slots(self) -> list:
        """
        识别本场技能位并按预设优先级排序；多次都认不出来返回空列表

        认不出来时**不盲点**技能位：技能位上带的可能是物品/药品，
        盲点会白耗道具（战车战场第 7 位实测就是物品），宁可这一场不打。
        """
        for attempt in range(1, self.SLOT_RECOGNIZE_ATTEMPTS + 1):
            self.screenshot()
            slots = self.battle_plan_slots()
            if slots:
                logger.info(f'本场技能位：{[slot + 1 for slot in slots]}')
                return slots
            logger.warning(f'技能位没识别出来（第 {attempt}/{self.SLOT_RECOGNIZE_ATTEMPTS} 次），'
                           f'等 {self.SLOT_RECOGNIZE_WAIT}s 再试')
            self.device.sleep(self.SLOT_RECOGNIZE_WAIT)
        return []

    def preset_battle_loop(self, coord, tolerance: int, end: datetime,
                           names: list = None, cfg=None) -> None:
        """
        按战斗预设打怪（队列式释放）

        循环体：锁一只白名单里的怪 -> 按预设优先级连续放技能，直到目标丢失
        （怪死了 / 被拉走）-> 回头锁下一只。

        与旧版的区别：**打不死的怪不会被每轮切掉**，技能一直往它身上放；
        只有目标丢失才换（鬼王这种要多打的怪能正常磨死）。

        - 技能位只在第一次锁到目标后识别一次（一场里技能栏不会变），认不出来就多试几次；
        - 一直认不出来时**不盲点技能位**（可能是物品/药品），本时段收工；
        - 位置检查 / 中途修理按累计释放次数触发（见 on_cast）；
        - **不判「清完了」**：时段内怪物一直刷新，读到 0 也只是瞬间，一律打满时段
          （11:30 / 16:30 到点由 battle_stop_reason 结束）。
        """
        names = self.battle_target_names() if names is None else names
        include_attack = self.battle_use_normal_attack()
        preset = self.battle_preset()
        logger.hr(f'开始按预设打怪（{preset["name"]}）')
        logger.attr('普通攻击', '开启' if include_attack else '关闭（技能全冷却时只能干等）')
        self.reset_battle_progress(end)

        slots = []
        progress = {'casts': 0, 'last_repair': datetime.now()}

        def on_cast(_casts: int) -> None:
            """每成功释放一次技能：按累计次数做位置检查 / 中途修理"""
            progress['casts'] += 1
            if progress['casts'] % self.RECOVER_EVERY_CASTS:
                return
            self.recover_in_battle(coord, tolerance)
            if self.need_repair(cfg, progress['last_repair'], end):
                self.repair_in_battle(coord, tolerance)
                progress['last_repair'] = datetime.now()
            logger.attr('打怪进度', f'casts={progress["casts"]}')

        with suppress_control_input_log():
            while datetime.now() < end:
                self.reset_records()
                if not self.acquire_target(names):
                    logger.info('附近没有白名单里的可攻击目标，等 1s 再找')
                    self.device.sleep(1)
                    continue

                if not slots:
                    slots = self.recognize_skill_slots()
                    if not slots:
                        if self.battle_has_pending_slots():
                            logger.info('技能位都在冷却中未识别，等冷却结束后重认')
                        else:
                            logger.warning('技能位一直认不出来（技能栏被挡 / 素材不匹配），'
                                           '本时段不再盲点技能位，收工')
                            break

                # 未识别且在冷却的技能位会在循环里冷却结束后重认并补进计划
                slots = self.battle_current_slots() or slots
                reason = self.battle_skill_loop(
                    slots,
                    check_stop=self.battle_stop_reason,
                    target_check=lambda image: self.battle_target_ok(names, image),
                    include_attack=include_attack,
                    attack_interval=self.NORMAL_ATTACK_INTERVAL,
                    poll_interval=self.SKILL_COOLDOWN_POLL,
                    on_cast=on_cast,
                    on_target_lost=self.battle_on_target_lost(names))

                if reason == 'window_end':
                    break
                if reason == 'timeout':
                    logger.warning('技能循环超时，重新锁目标继续')
                # 'target_lost'：怪死了 / 被拉走，回去锁下一只；给一点间隔别空转
                self.device.sleep(0.2)

        logger.attr('打怪结束', f'共释放 {progress["casts"]} 次技能')

    # ---------------------------------------------------------------- 打怪：停止条件
    def battle_stop_reason(self, image=None):
        """
        预设模式的停止条件（battle_skill_loop 每个循环都会调一次）

        只有时段到点才停：怪物在时段内一直刷新，不存在「打完了」这种状态。

        :param image: 调用方传进来的当帧截图（同一帧已经用来识别目标/冷切）
        :return: 'window_end' 时段到点 / None 继续打
        """
        self.reset_records()
        end = self._battle_deadline
        if end is not None and datetime.now() >= end:
            logger.info('时段到点，停止打怪')
            return 'window_end'
        return None

    # 时段剩余少于这个秒数就不中途修理了（修一次要十几秒，留给打怪）
    REPAIR_MIN_REMAIN = 180

    def need_repair(self, cfg, last_repair: datetime, end: datetime) -> bool:
        """是否到了中途修理的时间：勾选 + 距上次修理超过间隔 + 时段剩余充足"""
        if cfg is None or not cfg.repair_enable:
            return False
        if datetime.now() - last_repair < timedelta(minutes=int(cfg.repair_interval_minutes)):
            return False
        return (end - datetime.now()).total_seconds() > self.REPAIR_MIN_REMAIN

    def repair_in_battle(self, coord, tolerance: int) -> None:
        """
        打怪中途修理装备：背包随身功能一键修理（角色站桩不动，修完原地继续打）

        修理是纯 UI 操作，角色坐标不变；修完收起菜单（露出「目标」按钮），
        再回一次基准点（站桩挨打可能被推离），失败也不阻塞后续攻击。
        """
        logger.hr('打怪中途修理装备')
        try:
            ok = self.bag_repair_equipment()
            logger.attr('中途修理', '成功' if ok else '失败，继续打怪')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError) as e:
            logger.warning(f'中途修理异常，继续打怪: {e}')
        self.ui_close_menu()
        self.recover_position(coord, tolerance)

    # ---------------------------------------------------------------- 死亡
    def handle_death(self) -> None:
        """
        角色死亡：点「返回村子」复活后**原地继续打**，不重跑任务

        迷窟里的「返回村子」是原地复活（复活后仍在域外迷窟），走全局默认的
        「复活 + 重跑任务」反而更糟：重跑要重新准备/进图，而且 `run()` 的 finally
        一定会把 next_run 重排到下个时段，把重跑设置的「立即运行」覆盖掉，
        任务当天直接结束（实测：15:58 死亡后直接被排到第二天 10:27）。
        复活后位置跑偏由循环内的 recover_in_battle 兜底，这里不用处理。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        logger.warning('Character died, revive and continue')
        self.revive()
        self.handle_death_resume()

    def handle_death_resume(self) -> None:
        """死亡复活 / 掉线重启后：继续打怪（跑偏由 recover_in_battle 兜底）"""
        # 等复活/地图加载稳定，加载中点击会丢
        self.device.sleep(1)

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
        from tasks.GameUi.pages.page import page_main
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

        滚动方式见 minimap_npc_swipe_down：慢速受控拖动 + 等停稳，
        避免列表被甩过头导致目标行在两帧之间被跳过。
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
        """
        NPC 列表往下滚 2 行（156px）

        必须用「拖动到底按住再抬手」而不是快速滑动：minitouch 的 swipe 一滑即抬，
        实测同样拖 240px 会因惯性甩出整整一屏（>429px），目标行在两次采样之间被跳过；
        按住 0.4s 再抬手则惯性被吃掉，实测拖 156px 只滚 ~215px（≈2.8 行 < 一屏），
        相邻两次采样必定有重叠。滚完等列表停稳（实测 0.6~0.9s）再让上层识别。
        """
        x0, y0, w, h = self.NPC_LIST_REGION
        center_x = x0 + w // 2
        distance = self.NPC_LIST_ROW_PITCH * self.NPC_LIST_SWIPE_ROWS
        start_y = y0 + h - 40
        end_y = start_y - distance
        logger.info(f'Scroll NPC list: ({center_x},{start_y}) -> ({center_x},{end_y}) hold={self.NPC_LIST_HOLD}s')
        self.device.hold_drag((center_x, start_y), (center_x, end_y),
                              hold=self.NPC_LIST_HOLD, control_name='minimap_npc_list')
        self.device.click_record_clear()
        self.device.sleep(self.NPC_LIST_SETTLE_SLEEP)

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

    # NPC 对话选项区滚动：每步距离 / 最多滚多少步 / 拖动时长 / 停稳等待
    # 选项列表没有惯性，普通 swipe 松手即停；起止点都落在列表内部，避免点到滚动条
    OPTION_SCROLL_DISTANCE = 140
    OPTION_SCROLL_MAX = 6
    OPTION_SCROLL_DURATION = 0.25
    OPTION_SCROLL_SETTLE = 0.4

    def read_dialog_options(self) -> list[tuple[str, int, int]]:
        """
        用 Full 模式 OCR 当前可见的 NPC 对话选项

        :return: [(文字, 屏幕x, 屏幕y)]，坐标是文字框中心，可直接点击
        """
        self.screenshot()
        roi = self.O_NPC_DIALOG_OPTIONS.roi
        options = []
        for item in self.O_NPC_DIALOG_OPTIONS.detect_and_ocr(self.device.image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text:
                continue
            xs = [point[0] for point in item.box]
            ys = [point[1] for point in item.box]
            x = int((min(xs) + max(xs)) / 2) + roi[0]
            y = int((min(ys) + max(ys)) / 2) + roi[1]
            options.append((text, x, y))
        return options

    @staticmethod
    def dialog_option_signature(options: list[tuple[str, int, int]]) -> tuple:
        """当前可见选项的文字签名，用于判断滚动是否已经到顶/到底"""
        return tuple(sorted(text for text, _, _ in options))

    def scroll_dialog_options(self, down: bool = True) -> bool:
        """
        把 NPC 对话选项列表滚动一屏

        :param down: True = 查看下面的选项（内容上移），False = 查看上面的选项
        :return: 滚动后画面发生变化返回 True；已经到顶/到底返回 False
        """
        x0, y0, w, h = self.O_NPC_DIALOG_OPTIONS.roi
        cx = x0 + w // 2
        if down:
            p1 = (cx, y0 + h - 30)
            p2 = (cx, max(y0 + 30, p1[1] - self.OPTION_SCROLL_DISTANCE))
        else:
            p1 = (cx, y0 + 30)
            p2 = (cx, min(y0 + h - 30, p1[1] + self.OPTION_SCROLL_DISTANCE))

        before = self.dialog_option_signature(self.read_dialog_options())
        logger.info(f'Scroll dialog options {"down" if down else "up"}: {p1} -> {p2}')
        self.device.swipe(p1=p1, p2=p2, duration=self.OPTION_SCROLL_DURATION,
                          control_name='mimang_dialog_option_scroll')
        self.device.click_record_clear()
        self.device.sleep(self.OPTION_SCROLL_SETTLE)
        after = self.dialog_option_signature(self.read_dialog_options())
        if after == before:
            logger.info('Dialog options do not scroll any more (reached top/bottom)')
            return False
        return True

    def find_dialog_option(self, option: str, retry: int = 2) -> tuple[int, int] | None:
        """
        在 NPC 对话选项里找 option 的文字位置（列表可上下滚动）

        先看当前视图；找不到就向下滚到底，再向上滚回顶部，
        这样无论目标在当前位置的上方还是下方都能覆盖到。

        :param option: 要查找的选项文字（如「进入域外迷窟」）
        :param retry: 当前视图 OCR 重试次数（防止单帧识别抖动）
        :return: 文字框中心 (屏幕x, 屏幕y)；找不到返回 None
        """
        for attempt in range(1, retry + 1):
            self.reset_records()
            options = self.read_dialog_options()
            logger.info(f'Dialog options: {[text for text, _, _ in options]}')
            for text, x, y in options:
                if self.ocr_name_match(text, option):
                    logger.info(f'Dialog option [{text}] found at ({x},{y})')
                    return x, y
            logger.warning(f'Dialog option [{option}] not found in current view '
                           f'(attempt {attempt}/{retry})')
            self.device.sleep(0.4)

        for direction, label in ((True, 'down'), (False, 'up')):
            for step in range(1, self.OPTION_SCROLL_MAX + 1):
                if not self.scroll_dialog_options(down=direction):
                    break
                options = self.read_dialog_options()
                logger.info(f'Dialog options after scroll {label} {step}: '
                            f'{[text for text, _, _ in options]}')
                for text, x, y in options:
                    if self.ocr_name_match(text, option):
                        logger.info(f'Dialog option [{text}] found at ({x},{y}) '
                                    f'after scroll {label} {step}')
                        return x, y
        logger.warning(f'Dialog option [{option}] not found after scrolling')
        return None

    def click_enter_mimang(self, enter_text: str, enter_wait: int = 2) -> bool:
        """OCR 找到「进入域外迷窟」的文字位置并点击，随后等地图加载"""
        logger.hr(f'Click [{enter_text}]')
        coord = self.find_dialog_option(enter_text)
        if coord is None:
            logger.warning(f'Enter option [{enter_text}] does not appear')
            return False
        logger.info(f'Click enter option at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='mimang_enter')
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
        ScriptTask(config, device).click_enter_mimang('进入域外迷窟', enter_wait=2)
    except TaskEnd:
        pass
