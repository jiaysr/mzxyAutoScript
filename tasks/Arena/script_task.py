# This Python file uses the following encoding: utf-8
"""
同服竞技（2人角斗）：

1. 仅开放时间 12:00-22:00 运行，每天最多完成 count 次
2. 进入挑战-战场页面，确认右侧选中的是「同服竞技」（左侧出现 2人角斗 按钮）
3. 双击 2人角斗 按钮弹出报名弹窗，点击「确定」报名
4. 报名成功后自动回到主页面，每 2 秒检测一次匹配成功弹窗
5. 匹配成功后点击「确定」参战，等待 2 秒后重启游戏
6. 重启成功记完成 1 次；未达次数则立刻开下一局，达到次数则排到明天 12:00
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

    # 报名确认弹窗与匹配成功弹窗的文案
    QUEUE_TEXT = '是否进入2人角斗队列'
    MATCH_TEXT = '角斗场为您找到了对手'

    def run(self) -> None:
        arena = self.config.arena.arena_config
        now = datetime.now()

        # 开放时间 12:00-22:00
        if not (OPEN_TIME <= now.time() < CLOSE_TIME):
            logger.info(f'Arena is closed ({OPEN_TIME.strftime("%H:%M")}~{CLOSE_TIME.strftime("%H:%M")})')
            self.finish_today(now)
            raise TaskEnd('Arena')

        # 今日次数已达上限
        if arena.completed >= arena.count:
            logger.info(f'Completed count reached: {arena.completed}/{arena.count}')
            self.finish_today(now)
            raise TaskEnd('Arena')

        # 一局：报名 -> 匹配 -> 参战 -> 重启
        self.enter_arena_page()
        self.select_same_server()
        self.sign_up()
        if not self.wait_match():
            logger.warning('Match not found, give up this round')
            self.set_next_run(task='Arena', target=datetime.now() + timedelta(minutes=5))
            raise TaskEnd('Arena')

        self.device.sleep(2)
        self.restart_game()

        # 重启流程会重载配置，这里重新获取再计数
        arena = self.config.arena.arena_config
        arena.completed += 1
        self.config.save()
        logger.attr('Arena completed', f'{arena.completed}/{arena.count}')

        if arena.completed >= arena.count:
            logger.info('All arena rounds finished today')
            self.finish_today(datetime.now())
        else:
            # 立刻开下一局
            self.set_next_run(task='Arena', target=datetime.now() + timedelta(seconds=30))
        raise TaskEnd('Arena')

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

    def dialog_appear(self, text: str) -> bool:
        """
        精确检测弹窗文案（不用 ocr_appear：框架的 OCR filter 有逐字符兜底匹配，会误判）
        """
        results = self.O_ARENA_DIALOG.detect_and_ocr(self.device.image, logDisplay=False)
        return any(text in result.ocr_text for result in results)

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
        while not self.dialog_appear(self.QUEUE_TEXT):
            self.screenshot()
            if timer.reached():
                raise GameStuckError('Queue dialog does not appear')
            self.device.sleep(0.5)
        logger.info('Queue dialog appear, confirm sign up')
        self.click(self.C_ARENA_CONFIRM)
        # 报名成功后自动回到主页面
        self.ui_wait_until_appear(page_main, timeout=timeout)

    def wait_match(self) -> bool:
        """
        每 2 秒检测一次匹配成功弹窗，匹配成功后点「确定」参战
        :return: 匹配成功返回 True，超时返回 False
        """
        logger.hr('Wait for match')
        timeout = self.config.arena.arena_config.match_timeout
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.dialog_appear(self.MATCH_TEXT):
                logger.info('Match found, join the battle')
                self.click(self.C_ARENA_CONFIRM)
                return True
            if timer.reached():
                logger.warning(f'Wait match timeout ({timeout}s)')
                return False
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
