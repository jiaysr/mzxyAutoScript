# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
"""
明珠轩辕页面寻路引擎（门面）

能力按子包拆分，本类只做混入组装：
- tasks/GameUi/core/engine.py       寻路引擎：页面识别 / BFS / 路径执行 / 未知页脱困 / 统一操作入口
- tasks/GameUi/core/ocr.py          OCR 通用能力：名称匹配 / 彩色描边字 / 弹窗文案检测
- tasks/GameUi/core/utils.py         通用杂项：页面缓存 / 重启游戏 / 慢速滑动 / 任务让路判断
- tasks/GameUi/navigation/panel.py   角色面板左侧模块栏 / 顶部 tab 栏
- tasks/GameUi/navigation/top_menu.py 右上角菜单
- tasks/GameUi/navigation/activity.py 活动弹窗顶部 tab / 右侧子 tab / 活跃任务状态
- tasks/GameUi/navigation/bag.py     背包找物品 / 用物品
- tasks/GameUi/navigation/general.py 通用功能（背包修理装备等，各任务直接 self.bag_xxx() 调用）
- tasks/GameUi/navigation/battle.py  简单战斗系统（目标锁定 / 单次点击 / 循环放技能）
- tasks/GameUi/world/map.py          世界地图（小地图/大地图/列表/传送）
- tasks/GameUi/world/move.py         虚拟摇杆移动（主页面轮盘走到地图坐标）
- tasks/GameUi/pages/                页面注册表与页面定义（page.py / targets.py）
"""
from module.logger import logger
from tasks.GameUi.core.engine import NavigationEngine
from tasks.GameUi.core.ocr import OcrHelpers
from tasks.GameUi.core.utils import UiUtils
from tasks.GameUi.navigation.activity import ActivityNavigation
from tasks.GameUi.navigation.bag import BagNavigation
from tasks.GameUi.navigation.battle import SimpleBattle
from tasks.GameUi.navigation.general import GeneralFunctions
from tasks.GameUi.navigation.panel import PanelNavigation
from tasks.GameUi.navigation.top_menu import TopMenuNavigation
from tasks.GameUi.world.map import MapNavigation
from tasks.GameUi.world.move import MapMove


class GameUi(NavigationEngine, OcrHelpers, UiUtils,
             PanelNavigation, TopMenuNavigation, ActivityNavigation,
             BagNavigation, GeneralFunctions, MapNavigation, MapMove, SimpleBattle):
    """页面寻路引擎门面：能力见顶部文档，各子包混入使用"""

    def __init__(self, config, device):
        super().__init__(config, device)
        # 初始化时动态导入所有 page 模块
        self._import_all_pages()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from tasks.GameUi.pages.page import page_activity_notice, page_item_bag

    c = Config('oas1')
    d = Device(c)
    game = GameUi(config=c, device=d)
    game.ui_get_current_page()
    game.ui_goto(page_item_bag)
    logger.info(f'Registered pages: {[str(page) for page in game.ui_pages]}')
