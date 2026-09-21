# This Python file uses the following encoding: utf-8
"""
跨服竞技（2人跨服）：

1. 仅开放时间 13:00-14:00、17:00-18:00 运行，每天最多完成 count 次
2. 进入挑战-战场页面，确认右侧选中的是「跨服竞技」（左侧出现 2人跨服 按钮）
3. 双击 2人跨服 按钮弹出报名弹窗，点击「确定」报名
4. 报名成功后自动回到主页面，每 2 秒检测一次匹配成功弹窗（超时默认 20 分钟）
5. 匹配成功：点击「确定」参战，等待 2 秒后重启游戏，重启成功记完成 1 次
6. 匹配超时：重启游戏清掉排队状态，不计次，直接重新报名开下一局
7. 未达次数则立刻开下一局（同一任务内循环），达到次数则今天收工
8. 非开放时间：排到下一个开放时段；今天两个时段都过了则排到明天 13:00 并重置计数
9. 勾选「根据活跃度判断」后次数配置失效：每局开始前查活动-活跃页的「跨服竞技」是否已完成，
   已完成则直接收工，未完成才继续打
10. 活跃度模式下等待匹配时，如果有更高优先级的任务（Restart/Quiz/WorldBoss/Challenge/同服竞技）
    已到期，会放弃本轮（重启清队列）并排到 5 分钟后，给高优先级任务让路；未勾选时按默认等待到超时
"""
from datetime import datetime, time, timedelta

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.CrossArena.assets import CrossArenaAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_challenge

# 跨服竞技开放时段（每天两场）
OPEN_TIMES = [(time(13, 0), time(14, 0)), (time(17, 0), time(18, 0))]


class ScriptTask(GameUi, CrossArenaAssets):

    # 本任务在调度优先级表中的名字（用于让路判断）
    SCHEDULER_NAME = 'CrossArena'
    # 报名确认弹窗与匹配成功弹窗的文案
    QUEUE_TEXT = '进入2人跨服'
    # 匹配成功文案：OCR 会把「手」识成「于」、「入」识成「人」，这里用不易误识的片段
    MATCH_TEXTS = ['已找到对', '找到了对手']
    # 活跃页里对应的任务名
    ACTIVE_TASK_NAME = '跨服竞技'

    def run(self) -> None:
        # 一局结束后直接在同一任务内开下一局，直到次数/活跃任务完成、时段结束或需要让路
        while 1:
            arena = self.config.cross_arena.cross_arena_config
            now = datetime.now()

            # 开放时段 13:00-14:00 / 17:00-18:00
            if not self.in_open_time(now):
                target = self.next_open_time(now)
                logger.info(f'Cross arena is closed, next open: {target}')
                if target.date() != now.date():
                    # 今天的场次都结束了
                    if not arena.use_activity and arena.completed < arena.count:
                        logger.warning(f'Today cross arena not finished: {arena.completed}/{arena.count}')
                    arena.completed = 0
                    self.config.save()
                self.set_next_run(task='CrossArena', target=target, success=None, server=False)
                raise TaskEnd('CrossArena')

            if arena.use_activity:
                # 活跃度模式：活跃任务已完成则今天收工（次数配置失效）
                if self.active_task_completed(self.ACTIVE_TASK_NAME):
                    logger.info(f'Activity task [{self.ACTIVE_TASK_NAME}] completed, finish today')
                    self.finish_today(now)
                    raise TaskEnd('CrossArena')
            elif arena.completed >= arena.count:
                # 今日次数已达上限
                logger.info(f'Completed count reached: {arena.completed}/{arena.count}')
                self.finish_today(now)
                raise TaskEnd('CrossArena')

            # 一局：报名 -> 匹配 -> 参战 -> 重启
            self.enter_arena_page()
            self.select_cross_server()
            self.sign_up()
            result = self.wait_match()
            if result == 'yield':
                # 让路给高优先级任务：重启清掉排队状态，排到 5 分钟后
                self.restart_game()
                self.set_next_run(task='CrossArena', target=datetime.now() + timedelta(seconds=300))
                raise TaskEnd('CrossArena')

            # 匹配成功或超时都重启游戏清掉排队状态（超时不计次，直接重排下一局）
            if result == 'matched':
                self.device.sleep(2)
            self.restart_game()

            if result == 'matched':
                # 重启流程会重载配置，这里重新获取再计数
                arena = self.config.cross_arena.cross_arena_config
                arena.completed += 1
                self.config.save()
                logger.attr('Cross arena completed', f'{arena.completed}/{arena.count}')

    # ---------------------------------------------------------------- 调度
    @staticmethod
    def in_open_time(now: datetime) -> bool:
        """
        当前是否在开放时段内
        """
        return any(start <= now.time() < end for start, end in OPEN_TIMES)

    @staticmethod
    def next_open_time(now: datetime) -> datetime:
        """
        下一个开放时段的开始时间
        """
        for start, _ in OPEN_TIMES:
            target = datetime.combine(now.date(), start)
            if target > now:
                return target
        return datetime.combine(now.date() + timedelta(days=1), OPEN_TIMES[0][0])

    def finish_today(self, now: datetime) -> None:
        """
        今日收工：重置计数并排到明天 13:00
        """
        target = datetime.combine(now.date(), OPEN_TIMES[0][0])
        if target <= now:
            target += timedelta(days=1)
        logger.attr('Next cross arena start', target)
        self.config.cross_arena.cross_arena_config.completed = 0
        self.config.save()
        self.set_next_run(task='CrossArena', target=target, success=None, finish=True, server=False)

    # ---------------------------------------------------------------- 页面
    def enter_arena_page(self, timeout: int = 40) -> None:
        """
        前往挑战-战场页面
        """
        logger.hr('Enter cross arena page')
        if not self.ui_goto(page_challenge, timeout=timeout):
            raise GameStuckError('Challenge page does not appear')

    def select_cross_server(self, timeout: int = 15) -> None:
        """
        右侧未选中「跨服竞技」时点击它，直到左侧出现 2人跨服 按钮
        """
        logger.hr('Select cross server arena')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CROSS_ARENA_BUTTON_2P):
                logger.info('Cross server arena is selected')
                return
            if timer.reached():
                raise GameStuckError('Cross server arena not selected')
            self.click(self.C_CROSS_ARENA_SAME_SERVER, interval=1)

    def sign_up(self, timeout: int = 15) -> None:
        """
        双击 2人跨服 按钮弹出报名弹窗，点击「确定」报名
        """
        logger.hr('Sign up 2p cross arena')
        self.screenshot()
        if not self.appear(self.I_CROSS_ARENA_BUTTON_2P):
            raise GameStuckError('2p cross arena button not found')

        x, y = self.C_CROSS_ARENA_BUTTON_CLICK.coord()
        logger.info(f'Double click 2p cross arena button at ({x},{y})')
        self.device.click(x, y)
        self.device.sleep(0.1)
        self.device.click(x, y)
        self.device.click_record_clear()

        # 等待报名弹窗出现
        timer = Timer(timeout).start()
        while not self.dialog_appear(self.O_DIALOG_TEXT, self.QUEUE_TEXT):
            self.screenshot()
            if timer.reached():
                raise GameStuckError('Queue dialog does not appear')
            self.device.sleep(0.5)
        logger.info('Queue dialog appear, confirm sign up')
        self.click(self.C_DIALOG_CONFIRM)
        # 报名确认后弹窗自动关闭并回到主页面，直接进入匹配等待
        self.device.click_record_clear()
        self.device.sleep(1)

    def wait_match(self) -> str:
        """
        每 2 秒检测一次匹配成功弹窗，匹配成功后点「确定」参战
        活跃度模式下，若有更高优先级任务已到期则提前让路（重启清队列后重排）
        :return: 'matched' 匹配成功 / 'timeout' 等待超时 / 'yield' 让路给高优先级任务
        """
        logger.hr('Wait for match')
        arena = self.config.cross_arena.cross_arena_config
        timeout = arena.match_timeout
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if any(self.dialog_appear(self.O_DIALOG_TEXT, text) for text in self.MATCH_TEXTS):
                logger.info('Match found, join the battle')
                self.click(self.C_CROSS_ARENA_JOIN_CONFIRM)
                return 'matched'
            if timer.reached():
                logger.warning(f'Wait match timeout ({timeout}s)')
                # 超时打印当前弹窗文案，便于排查匹配文案是否变化
                for item in self.O_DIALOG_TEXT.detect_and_ocr(self.device.image, logDisplay=False):
                    logger.info(f'Dialog text: {item.ocr_text}')
                return 'timeout'
            if arena.use_activity and self.higher_priority_task_due():
                logger.warning('Higher priority task is due, give up this round')
                return 'yield'
            # 主页面静止等待匹配，需定期清空卡死记录
            self.reset_records()
            self.device.sleep(2)

    def restart_game(self) -> None:
        """
        复用重启任务的登录流程重启游戏（重启后清空页面缓存）
        """
        logger.hr('Restart game after cross arena')
        self.ui_restart_game()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
