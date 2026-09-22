# This Python file uses the following encoding: utf-8
"""
上古狩猎（AncientHunt）：

第一步：确保角色站在目标地点（默认牧野）的传送落点上
- 已经在该地点且在传送落点：不做任何操作
- 在该地点但不在传送落点：先传送到其他地点，再传送回来
- 不在该地点：直接传送

地点识别、传送与小地图/世界地图/列表导航统一由 GameUi（tasks/GameUi/map.py）提供，
各地点的传送落点是 2026-09-22 真机实测值（MAP_INITIAL_POS）。

活动开放提醒弹窗（popup_hunt_notice）由 GlobalGame 的全局弹窗处理自动关闭。

后续狩猎流程（寻找目标 / 战斗等）待补充。
"""
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main


class ScriptTask(GameUi):

    # 调度优先级名（ConfigManual.SCHEDULER_PRIORITY，用于长时间等待时给高优先级任务让路）
    SCHEDULER_NAME = 'AncientHunt'

    def run(self) -> None:
        target = self.config.ancient_hunt.ancient_hunt_config.target_location
        logger.hr(f'Ancient hunt prepare: {target} initial position')

        self.prepare_main_page()
        if not self.map_ensure_location_initial(target):
            logger.error(f'Unable to reach [{target}] initial position')
            self.set_next_run(task='AncientHunt', success=False, finish=True)
            raise TaskEnd('AncientHunt')

        # TODO: 上古狩猎后续流程（在目标地点寻找并狩猎）

        self.set_next_run(task='AncientHunt', success=True, finish=True)
        raise TaskEnd('AncientHunt')

    def prepare_main_page(self) -> None:
        """
        确保游戏在线且停留在主页面：地图导航要从主页面上的小地图入口开始，
        不在主页面（登录页/其他页面）先回主页面，回不去则重启游戏，
        再关掉会盖住小地图入口的活动弹窗
        """
        logger.hr('Prepare main page')
        if not self.device.app_is_running():
            logger.info('Game is not running, restart game')
            self.ui_restart_game()
            return

        self.ui_reset_current_page()
        try:
            if self.ui_goto(page_main, timeout=30):
                # ui_goto 只看主页面右下角特征，活动弹窗开着也算到达，这里再关掉遮挡弹窗
                self.map_close_main_popup()
                logger.info('Game is on the main page')
                return
            logger.warning('Failed to go back to the main page, restart game')
        except (GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'Main page is not reachable ({e}), restart game')
        self.ui_restart_game()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
