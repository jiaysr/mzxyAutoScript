# This Python file uses the following encoding: utf-8
"""
副本玩法 - 通用逻辑（DungeonBase）

所有具体副本共用的能力都放这里；具体副本（tasks/Dungeon/dungeons/*.py）继承
DungeonBase，只实现自己的差异，避免每个副本各写一套。

新增副本：
- 继承 DungeonBase，覆盖 name，实现 run()
- 「准备主页面 / 回主页面」等直接调用本类方法即可
"""
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError)
from module.logger import logger
from tasks.Dungeon.assets import DungeonAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main


class DungeonBase(GameUi, DungeonAssets):
    """副本通用基类：具体副本继承它并实现 run()"""

    # 副本名（子类覆盖，仅用于日志/提示）
    name: str = '副本'

    def run(self) -> None:
        """
        具体副本流程入口，由子类实现。

        推荐骨架（按需取用，很多能力来自 GameUi）：
            self.prepare()            # 确保在线且在主页面
            self.enter_dungeon()      # 进入副本（页面导航 / 点入口）
            self.battle()             # 副本内战斗
            self.settle()             # 结算/领奖
            self.back_to_main()       # 回主页面
        """
        raise NotImplementedError(f'{type(self).__name__}.run is not implemented')

    # ---------------------------------------------------------------- 通用能力
    def prepare(self, timeout: int = 60) -> None:
        """确保游戏在线并停留在主页面（不在主页面先回，回不去则重启游戏）"""
        logger.hr(f'[{self.name}] 准备主页面')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            return
        self.ui_reset_current_page()
        try:
            if self.ui_goto(page_main, timeout=timeout):
                # 关掉会盖住入口的主页面弹窗（活动弹窗等）
                self.map_close_main_popup()
                return
            logger.warning('无法切回主页面，重启游戏')
        except (GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'当前页面异常（{e}），重启游戏')
        self.ui_restart_game()

    def back_to_main(self, timeout: int = 30) -> None:
        """回到主页面（副本结束后用）"""
        logger.hr(f'[{self.name}] 回主页面')
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=timeout):
            raise GameStuckError('Main page does not appear')
