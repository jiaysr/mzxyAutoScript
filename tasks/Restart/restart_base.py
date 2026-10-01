# This Python file uses the following encoding: utf-8
from datetime import datetime

from module.exception import (GameStuckError,
                              GameTooManyClickError,
                              RequestHumanTakeover,
                              TaskEnd)
from module.logger import logger
from tasks.Restart.assets import RestartAssets
from tasks.base_task import BaseTask


class RestartBase(BaseTask, RestartAssets):
    """
    重启任务的公共部分：启停游戏、登录重试、延迟待办
    游戏客户端相关的登录流程由 RestartX7 / RestartOfficial 实现
    """

    login_retry = 2

    def run(self) -> None:
        if not self.delay_pending_tasks():
            self.app_restart()
        raise TaskEnd('ScriptTask end')

    def app_stop(self) -> None:
        logger.hr('App stop')
        self.device.app_stop()

    def app_start(self) -> None:
        logger.hr('App start')
        self.device.app_start()
        self.app_handle_login()
        self.after_login()

    def app_restart(self) -> None:
        logger.hr('App restart')
        self.device.app_stop()
        self.device.app_start()
        self.app_handle_login()
        self.set_next_run(task='Restart', success=True, finish=True, server=True)
        self.after_login()

    def app_handle_login(self) -> bool:
        """
        登录入口，失败时重启游戏重试
        """
        for _ in range(self.login_retry):
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            try:
                self._app_handle_login()
                return True
            except (GameStuckError, GameTooManyClickError) as e:
                logger.warning(e)
                self.device.app_stop()
                self.device.app_start()

        logger.critical(f'Login failed more than {self.login_retry}')
        logger.critical('Game server may be under maintenance, or you may lost network connection')
        raise RequestHumanTakeover

    def _app_handle_login(self) -> bool:
        """
        具体登录流程，由游戏客户端版本实现
        """
        raise NotImplementedError

    def after_login(self) -> None:
        """
        登录成功后的收尾（领取奖励、定时安排等），由游戏客户端版本按需实现
        """

    def delay_pending_tasks(self) -> bool:
        """
        周三更新游戏的时候延迟
        :return: 已延迟返回 True
        """
        datetime_now = datetime.now()
        if not (datetime_now.weekday() == 2 and 6 <= datetime_now.hour <= 8):
            return False
        logger.info('The game server is updating, delay the pending tasks to 9:00')
        logger.warning('Delay pending tasks')
        for task in self.config.pending_task:
            logger.attr('Delay', task.command)
            self.set_next_run(task=task.command,
                              target=datetime_now.replace(hour=9, minute=0, second=0, microsecond=0))
        self.set_next_run(task='Restart', success=True, finish=True, server=True)
        return True
