# This Python file uses the following encoding: utf-8
"""
世界任务（WorldTask）：每天一次，只在开放世界时段（09:00-24:00）里跑

框架（玩法待补）：
- 排期：run() 结束时 schedule_next_run() 按「成功/失败间隔（默认各 1 天）+ 开放世界窗口」
  算下次运行时间，落在 00:00-08:59 的一律顺延到当天 09:00（见 tasks/WorldTask/schedule.py）；
- 早退：被手动运行或别的任务 task_call 提前叫醒、而开放世界还没开门时，只重排到最近
  一次开门，不做任何游戏内操作（门没开，点什么都是白点）；
- 玩法：world_task_flow() 先占位，等游戏里的实际流程确认后再一步步补。

排期为什么不直接 set_next_run(success=True)：默认路径按 success_interval 直接相加，
不认开放世界窗口，凌晨跑过一次就会把下次排到凌晨（窗口外，永远跑不了），
所以这里自己算出窗口内的 target，再用 server=False 写入。
"""
from datetime import datetime

from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.WorldTask.schedule import clamp_to_open_world, in_open_world, next_run_after


class ScriptTask(GameUi):
    SCHEDULER_NAME = 'WorldTask'
    TASK_NAME = 'WorldTask'

    def run(self) -> None:
        logger.hr('世界任务')
        if not in_open_world(datetime.now()):
            self.schedule_wait_open_world()
            logger.warning('开放世界还没开门（09:00-24:00），本次不执行，已排到开门时间')
            raise TaskEnd(self.TASK_NAME)

        success = False
        try:
            self.prepare_game()
            success = self.world_task_flow()
        finally:
            # 正常/异常都要重排，避免 next_run 停在过去霸占调度
            self.schedule_next_run(success=success)
        raise TaskEnd(self.TASK_NAME)

    # ---------------------------------------------------------------- 玩法（待补）
    def world_task_flow(self) -> bool:
        """
        世界任务的游戏流程：成功返回 True

        目前是框架阶段的占位实现：不报错（否则会走到 Script 的致命异常分支），
        只记一条日志，按失败间隔重排，等玩法确定后在这里逐个补。
        """
        logger.warning('世界任务玩法还没实现，本次只做框架校验（进游戏 + 排期）')
        return False

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
            from tasks.GameUi.pages.page import page_main
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=60):
                raise GameStuckError('Main page does not appear')
            logger.info('游戏在线且在主页面')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError):
            logger.warning('无法切回主页面，重启游戏')
            self.ui_restart_game()

    # ---------------------------------------------------------------- 排期
    def schedule_next_run(self, success: bool = True) -> None:
        """下次 = 现在 + 成功/失败间隔，再夹进开放世界时段"""
        scheduler = self.config.model.world_task.scheduler
        interval = scheduler.success_interval if success else scheduler.failure_interval
        next_run = next_run_after(datetime.now(), interval,
                                  float_seconds=self.float_seconds(scheduler))
        self.apply_next_run(next_run)
        logger.attr('WorldTask 排期', f'{next_run:%Y-%m-%d %H:%M:%S} (success={success})')

    def schedule_wait_open_world(self) -> None:
        """窗口没开：排到最近一次开门（当天 09:00，已经过了就次日 09:00）"""
        scheduler = self.config.model.world_task.scheduler
        next_run = clamp_to_open_world(datetime.now(),
                                       float_seconds=self.float_seconds(scheduler))
        self.apply_next_run(next_run)
        logger.attr('WorldTask 排期', f'{next_run:%Y-%m-%d %H:%M:%S} (wait open world)')

    def apply_next_run(self, next_run: datetime) -> None:
        """写入下次运行时间：时间已经算好，不再走默认的服务器刷新逻辑（server=False）"""
        self.set_next_run(task=self.TASK_NAME, target=next_run, success=None,
                          finish=True, server=False)

    @staticmethod
    def float_seconds(scheduler) -> int:
        """排期的浮动时间（秒）：被夹到 09:00 整时随机后移，避免所有任务同一秒启动"""
        float_time = getattr(scheduler, 'float_time', None)
        if float_time is None:
            return 0
        return float_time.hour * 3600 + float_time.minute * 60 + float_time.second


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
