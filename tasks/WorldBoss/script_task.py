# This Python file uses the following encoding: utf-8
import re
from datetime import datetime, time, timedelta

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_activity_world_boss
from tasks.WorldBoss.assets import WorldBossAssets

# 世界首领：目标地图与坐标来自游戏内「寻」的自动寻路终点（实测值）
BOSS_LIST = [
    {
        'key': 'manchui',
        'name': '蛮锤',
        'target_map': '牧野',
        'target_coord': (272, 70),
        'times': [('time_09', time(9, 0)), ('time_13', time(13, 0))],
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


class ScriptTask(GameUi, WorldBossAssets):
    """
    世界首领：按配置的时间段提前出发，自动寻路到首领位置
    角色阵亡由 BaseTask.screenshot 的全局死亡检测处理（复活后重跑本任务）
    """

    # 首领出现时长：事件时间之后超过该时长首领已消失，直接跳过本次事件
    event_window = timedelta(minutes=5)
    # 攻击间隔（秒），截图与目标确认的耗时算在间隔内
    attack_interval = 0.5
    # 攻击次数上限，打满即视为本流程结束
    attack_times = 15
    # 当前事件的标识与已攻击次数，阵亡重跑时用于恢复进度
    event_key: str = ''
    attack_count: int = 0

    def run(self) -> None:
        self.reset_records()
        events = self.current_events()
        if not events:
            logger.info('No world boss event now')
            self.clear_task_record()
            self.schedule_next_event()
            raise TaskEnd('WorldBoss')

        for index, (boss, event_time) in enumerate(events, start=1):
            logger.hr(f"World boss {boss['name']} {event_time.strftime('%H:%M')} ({index}/{len(events)})")
            self.event_key = f"{boss['key']} {event_time.strftime('%Y-%m-%d %H:%M')}"
            self.attack_count = 0
            if self.restore_task_record():
                logger.attr('Restore attack count', self.attack_count)
            self.world_boss_flow(boss, event_time)

        self.clear_task_record()
        self.schedule_next_event()
        raise TaskEnd('WorldBoss')

    # ---------------------------------------------------------------- 进度记录
    def save_task_record(self) -> dict:
        """
        阵亡重跑时保留当前事件与已攻击次数
        """
        return {'event': self.event_key, 'attack_count': self.attack_count}

    def load_task_record(self, record: dict) -> bool:
        """
        只有同一个事件的记录才恢复，避免把上一次事件的次数带到本次
        """
        if record.get('event') != self.event_key:
            return False
        self.attack_count = int(record.get('attack_count', 0))
        return True

    def world_boss_flow(self, boss: dict, event_time: datetime) -> None:
        """
        一次完整的首领流程：主界面出发 -> 寻路 -> 等到刷新 -> 锁定 -> 攻击
        """
        self.enter_world_boss_page()
        if not self.seek_boss(boss):
            logger.warning(f"Unable to seek {boss['name']}")
            return

        if not self.wait_until_arrive(boss):
            logger.warning(f"Did not arrive at {boss['target_map']}{boss['target_coord']}")

        if not self.lock_boss(boss, event_time):
            logger.warning(f"Unable to lock {boss['name']}")
            return

        self.attack_boss(boss)

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

    def event_datetimes(self) -> list:
        """
        今天与明天的全部事件时间
        """
        today = datetime.now().date()
        result = []
        for boss, event_time in self.iter_enabled_events():
            for day in (0, 1):
                result.append((boss, datetime.combine(today + timedelta(days=day), event_time)))
        return result

    def current_events(self) -> list:
        """
        当前处于（出发时间 ~ 首领消失）内的全部事件，按事件时间排序
        首领只出现 event_window，现在距离事件时间超过该时长则视为已错过
        同一时间段可能出现多个首领（如 19:00 的无情与巨镰），都勾选时全部返回
        """
        now = datetime.now()
        result = []
        for boss, event_datetime in self.event_datetimes():
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
    def enter_world_boss_page(self, timeout: int = 40) -> bool:
        """
        前往活动-世界首领页面（页面寻路自动处理：展开菜单 -> 活动图标 -> 活动 tab -> 子 tab）
        """
        logger.hr('Enter world boss page')
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
        读取当前锁定目标的名称
        """
        results = self.O_TARGET_NAME.detect_and_ocr(self.device.image, logDisplay=False)
        return ''.join(result.ocr_text for result in results)

    def target_keywords(self, boss: dict) -> list:
        """
        锁定目标的名称匹配关键字：配置优先，未配置时使用首领名称
        """
        configured = self.config.world_boss.world_boss_config.target_name.strip()
        if configured:
            return [word.strip() for word in configured.split(',') if word.strip()]
        return [boss['name']]

    def lock_boss(self, boss: dict, event_time: datetime, timeout: int = 30) -> bool:
        """
        等到首领刷新后收起菜单，点击目标按钮锁定首领
        只有目标名称匹配才算锁定成功
        """
        logger.hr(f"Lock {boss['name']}")
        self.wait_until_spawn(event_time)
        self.collapse_menu()

        keywords = self.target_keywords(boss)
        logger.attr('Target keywords', ', '.join(keywords))

        timer = Timer(timeout).start()
        target_name = ''
        while 1:
            self.reset_records()
            self.screenshot()
            target_name = self.get_target_name()
            if target_name:
                logger.attr('Target', target_name)
                if any(word in target_name for word in keywords):
                    logger.info(f"Boss name matched: {target_name}")
                    return True
            if timer.reached():
                logger.warning(f"Boss name not matched, last target: {target_name}")
                return False
            if self.appear_then_click(self.I_TARGET_BUTTON,
                                      action=self.C_TARGET_BUTTON_CLICK,
                                      interval=1):
                logger.info('Click target button')
                continue
            self.device.sleep(0.5)
    def target_locked(self, keywords: list) -> bool:
        """
        当前锁定目标的名称是否匹配
        """
        target_name = self.get_target_name()
        if target_name:
            logger.attr('Target', target_name)
        return any(word in target_name for word in keywords)

    def attack_boss(self, boss: dict, times: int = None) -> bool:
        """
        锁定目标后循环攻击：点击攻击区域 -> 间隔 0.5s -> 重新截图确认目标仍在锁定
        只有目标仍在锁定状态时才继续攻击，打满次数上限即本流程结束
        阵亡重跑后从上次已攻击次数继续，保证同一个事件总攻击次数不超过上限
        """
        logger.hr(f"Attack {boss['name']}")
        keywords = self.target_keywords(boss)
        times = times or self.attack_times
        done = self.attack_count
        if done >= times:
            logger.info(f'Attack already finished, {done} attacks')
            return True

        self.reset_records()
        self.screenshot()
        if not self.target_locked(keywords):
            logger.warning('Target is not locked before attack')
            return False

        for attack_count in range(done + 1, times + 1):
            started = datetime.now()
            self.reset_records()
            self.click(self.C_ATTACK)
            self.screenshot()
            self.attack_count = attack_count

            if not self.target_locked(keywords):
                logger.info(f'Target lost after {attack_count} attacks, stop')
                return True
            if attack_count >= times:
                logger.info(f'Attack finished, {attack_count} attacks')
                return True

            # 补足攻击间隔到 0.5s（截图耗时算在内）
            elapsed = (datetime.now() - started).total_seconds()
            if elapsed < self.attack_interval:
                self.device.sleep(self.attack_interval - elapsed)

    # ---------------------------------------------------------------- 到达
    def get_position(self):
        """
        读取小地图下方的当前坐标，返回 (地图名, (x, y))
        """
        results = self.O_POSITION.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(result.ocr_text for result in results)
        match = re.search(r'([^\d]+?)(\d+)[,.](\d+)', text)
        if not match:
            return None, None
        map_name = re.sub(r'[^\u4e00-\u9fa5]', '', match.group(1))
        return map_name, (int(match.group(2)), int(match.group(3)))

    def wait_until_arrive(self, boss: dict, timeout: int = 180) -> bool:
        """
        等待自动寻路到达首领坐标
        """
        logger.hr('Wait until arrive')
        target_map = boss['target_map']
        target_coord = boss['target_coord']
        timer = Timer(timeout).start()

        while 1:
            self.reset_records()
            self.screenshot()
            map_name, coord = self.get_position()
            if map_name is None:
                if timer.reached():
                    raise GameStuckError('Unable to read character position')
                self.device.sleep(1)
                continue

            distance = abs(coord[0] - target_coord[0]) + abs(coord[1] - target_coord[1])
            logger.attr('Position', f'{map_name}{coord[0]},{coord[1]} distance {distance}')
            if target_map in map_name and distance <= ARRIVE_TOLERANCE:
                logger.info(f"Arrived at {map_name}{coord[0]},{coord[1]}")
                return True
            if timer.reached():
                logger.warning(f"Arrive timeout, now at {map_name}{coord[0]},{coord[1]}")
                return False
            self.device.sleep(1)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
