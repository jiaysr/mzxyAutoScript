# This Python file uses the following encoding: utf-8
"""
右上角菜单导航（TopMenuNavigation）：

菜单可以收起/展开，收起时菜单内图标不可见。
使用流程：先确保菜单展开，再在当前帧里查找目标图标并点击。
由 GameUi 混入本类使用。
"""
from time import sleep

from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class TopMenuNavigation(BaseTask, GameUiAssets):
    """右上角菜单（展开/收起 + 菜单图标）导航能力"""

    def ui_open_menu(self, timeout: float = 6) -> bool:
        """确保右上角菜单展开（处于收起状态时点击开关展开）"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_MENU_EXPANDED):
                return True
            if self.appear(self.I_MENU_COLLAPSED):
                logger.info('Expand top-right menu')
                self.appear_then_click(self.I_MENU_COLLAPSED, interval=0.5)
            sleep(0.2)
        logger.warning('Top-right menu not expanded')
        return False

    def ui_close_menu(self, timeout: float = 6) -> bool:
        """确保右上角菜单收起（处于展开状态时点击开关收起）"""
        timer = Timer(timeout).start()
        while not timer.reached():
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
