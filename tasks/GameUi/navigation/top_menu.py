# This Python file uses the following encoding: utf-8
"""
右上角菜单导航（TopMenuNavigation）：

菜单可以收起/展开，收起时菜单内图标不可见。
使用流程：先确保菜单展开，再在当前帧里查找目标图标并点击。
由 GameUi 混入本类使用。
"""
from time import sleep
from typing import Callable

from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class TopMenuNavigation(BaseTask, GameUiAssets):
    """右上角菜单（展开/收起 + 菜单图标）导航能力"""

    def menu_icons_visible(self) -> bool:
        """
        右上角菜单是否展开（以菜单图标是否可见为准）

        不要用箭头模板判断展开/收起：游戏更新后箭头方向会变，
        实测**展开状态匹配的是 I_MENU_COLLAPSED**（左箭头），
        按箭头判断会把已展开的菜单当成收起，反复点开关，
        最后触发连点保护（GameTooManyClickError）。
        """
        return (self.appear(self.I_MENU_BAG_ICON)
                or self.appear(self.I_ACTIVITY_ICON))

    def ui_open_menu(self, timeout: float = 6) -> bool:
        """确保右上角菜单展开（处于收起状态时点击开关展开）"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            # 图标可见 = 菜单已经展开，直接返回，绝不能再点箭头
            if self.menu_icons_visible():
                return True
            logger.info('Expand top-right menu')
            # 箭头方向随版本变化，哪个开关模板命中就点哪个
            if not self.appear_then_click(self.I_MENU_COLLAPSED, interval=0.5):
                self.appear_then_click(self.I_MENU_EXPANDED, interval=0.5)
            sleep(0.2)
        logger.warning('Top-right menu not expanded')
        return False

    def ui_close_menu(self, timeout: float = 6,
                      abort_check: Callable[[], bool] = None) -> bool:
        """
        确保右上角菜单收起（处于展开状态时点击开关收起）

        :param abort_check: 无参检查，返回 True 立刻放弃。
                            弹窗（结算/提示）把背景压暗时，收起/展开两个特征都匹配不上，
                            不放弃就必然一路空转到 timeout（实测白等 6s）
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            if abort_check is not None and abort_check():
                logger.info('Abort collapsing top-right menu')
                return False
            self.screenshot()
            if self.appear(self.I_MENU_COLLAPSED):
                return True
            if self.appear(self.I_MENU_EXPANDED):
                logger.info('Collapse top-right menu')
                self.appear_then_click(self.I_MENU_EXPANDED, interval=0.5)
            sleep(0.2)
        logger.warning('Top-right menu not collapsed')
        return False

    def ui_menu_click(self, icon, timeout: float = 6) -> bool:
        """展开右上角菜单后，查找并点击菜单内的图标"""
        if not self.ui_open_menu(timeout):
            return False
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(icon):
                x, y = icon.coord()
                logger.info(f'Click menu icon {icon.name} at ({x},{y})')
                self.device.click(x=x, y=y, control_name=icon.name)
                return True
            sleep(0.2)
        logger.warning(f'Menu icon {icon.name} not found')
        return False
