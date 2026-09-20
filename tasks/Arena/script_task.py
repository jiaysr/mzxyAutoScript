# This Python file uses the following encoding: utf-8
"""
同服竞技（2人角斗）：

1. 仅开放时间 12:00-22:00 运行，每天最多完成 count 次
2. 进入挑战-战场页面，确认右侧选中的是「同服竞技」（左侧出现 2人角斗 按钮）
3. 双击 2人角斗 按钮弹出报名弹窗，点击「确定」报名
4. 报名成功后自动回到主页面，每 2 秒检测一次匹配成功弹窗（超时默认 20 分钟）
5. 匹配成功：点击「确定」参战，等待 2 秒后重启游戏，重启成功记完成 1 次
6. 匹配超时：重启游戏清掉排队状态，不计次，直接重新报名开下一局
7. 未达次数则立刻开下一局（同一任务内循环），达到次数则排到明天 12:00
8. 勾选「根据活跃度判断」后次数配置失效：每局开始前查活动-活跃页的「同服竞技」是否已完成，
   已完成则直接收工，未完成才继续打
9. 活跃度模式下等待匹配时，如果有更高优先级的任务（Restart/Quiz/WorldBoss/Challenge）已到期，
   会放弃本轮（重启清队列）并排到 5 分钟后，给高优先级任务让路；未勾选时按默认等待到超时
"""
from datetime import datetime, time, timedelta

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.Arena.assets import ArenaAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_challenge, page_main
from tasks.Restart.script_task import ScriptTask as RestartTask

# 竞技场开放时间
OPEN_TIME = time(12, 0)
CLOSE_TIME = time(22, 0)


class ScriptTask(GameUi, ArenaAssets):

    # 本任务在调度优先级表中的名字（用于让路判断）
    SCHEDULER_NAME = 'Arena'
    # 报名确认弹窗与匹配成功弹窗的文案
    QUEUE_TEXT = '是否进入2人角斗队列'
    MATCH_TEXT = '角斗场为您找到了对手'
    # 活跃页里对应的任务名
    ACTIVE_TASK_NAME = '同服竞技'

    def run(self) -> None:
        # 一局结束后直接在同一任务内开下一局，直到次数/活跃任务完成或需要让路
        while 1:
            arena = self.config.arena.arena_config
            now = datetime.now()

            # 开放时间 12:00-22:00
            if not (OPEN_TIME <= now.time() < CLOSE_TIME):
                logger.info(f'Arena is closed ({OPEN_TIME.strftime("%H:%M")}~{CLOSE_TIME.strftime("%H:%M")})')
                self.finish_today(now)
                raise TaskEnd('Arena')

            if arena.use_activity:
                # 活跃度模式：活跃任务已完成则今天收工（次数配置失效）
                if self.active_task_completed(self.ACTIVE_TASK_NAME):
                    logger.info(f'Activity task [{self.ACTIVE_TASK_NAME}] completed, finish today')
                    self.finish_today(now)
                    raise TaskEnd('Arena')
            elif arena.completed >= arena.count:
                # 今日次数已达上限
                logger.info(f'Completed count reached: {arena.completed}/{arena.count}')
                self.finish_today(now)
                raise TaskEnd('Arena')

            # 一局：报名 -> 匹配 -> 参战 -> 重启
            self.enter_arena_page()
            self.select_same_server()
            self.sign_up()
            result = self.wait_match()
            if result == 'yield':
                # 让路给高优先级任务：重启清掉排队状态，排到 5 分钟后
                self.restart_game()
                self.set_next_run(task='Arena', target=datetime.now() + timedelta(seconds=300))
                raise TaskEnd('Arena')

            # 匹配成功或超时都重启游戏清掉排队状态（超时不计次，直接重排下一局）
            if result == 'matched':
                self.device.sleep(2)
            self.restart_game()

            if result == 'matched':
                # 重启流程会重载配置，这里重新获取再计数
                arena = self.config.arena.arena_config
                arena.completed += 1
                self.config.save()
                logger.attr('Arena completed', f'{arena.completed}/{arena.count}')

    # ---------------------------------------------------------------- 调度
    def finish_today(self, now: datetime) -> None:
        """
        排到明天 12:00 并重置今日计数
        """
        target = datetime.combine(now.date(), OPEN_TIME)
        if target <= now:
            target += timedelta(days=1)
        logger.attr('Next arena start', target)
        self.config.arena.arena_config.completed = 0
        self.config.save()
        self.set_next_run(task='Arena', target=target, success=None, finish=True, server=False)

    # ---------------------------------------------------------------- 页面
    def enter_arena_page(self, timeout: int = 40) -> None:
        """
        前往挑战-战场页面
        """
        logger.hr('Enter arena page')
        if not self.ui_goto(page_challenge, timeout=timeout):
            raise GameStuckError('Challenge page does not appear')

    def select_same_server(self, timeout: int = 15) -> None:
        """
        右侧未选中「同服竞技」时点击它，直到左侧出现 2人角斗 按钮
        """
        logger.hr('Select same server arena')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_ARENA_BUTTON_2P):
                logger.info('Same server arena is selected')
                return
            if timer.reached():
                raise GameStuckError('Same server arena not selected')
            self.click(self.C_ARENA_SAME_SERVER, interval=1)

    def sign_up(self, timeout: int = 15) -> None:
        """
        双击 2人角斗 按钮弹出报名弹窗，点击「确定」报名
        """
        logger.hr('Sign up 2p arena')
        self.screenshot()
        if not self.appear(self.I_ARENA_BUTTON_2P):
            raise GameStuckError('2p arena button not found')

        x, y = self.C_ARENA_BUTTON_CLICK.coord()
        logger.info(f'Double click 2p arena button at ({x},{y})')
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
        # 报名成功后自动回到主页面
        self.ui_wait_until_appear(page_main, timeout=timeout)

    def wait_match(self) -> str:
        """
        每 2 秒检测一次匹配成功弹窗，匹配成功后点「确定」参战
        活跃度模式下，若有更高优先级任务已到期则提前让路（重启清队列后重排）
        :return: 'matched' 匹配成功 / 'timeout' 等待超时 / 'yield' 让路给高优先级任务
        """
        logger.hr('Wait for match')
        arena = self.config.arena.arena_config
        timeout = arena.match_timeout
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.dialog_appear(self.O_DIALOG_TEXT, self.MATCH_TEXT):
                logger.info('Match found, join the battle')
                self.click(self.C_DIALOG_CONFIRM)
                return 'matched'
            if timer.reached():
                logger.warning(f'Wait match timeout ({timeout}s)')
                return 'timeout'
            if arena.use_activity and self.higher_priority_task_due():
                logger.warning('Higher priority task is due, give up this round')
                return 'yield'
            # 主页面静止等待匹配，需定期清空卡死记录
            self.reset_records()
            self.device.sleep(2)

    def restart_game(self) -> None:
        """
        复用重启任务的登录流程重启游戏
        """
        logger.hr('Restart game after arena')
        RestartTask(self.config, self.device).app_restart()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
