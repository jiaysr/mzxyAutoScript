# -*- coding: utf-8 -*-
"""小地图移动的「游戏进程退出」回归测试（不连设备）"""
from types import SimpleNamespace

import pytest

from module.exception import GameNotRunningError
from tasks.GameUi.world.move import MapMove


class FakeMove:
    device = SimpleNamespace(app_is_running=lambda: False)


def test_map_move_ensure_main_page_raises_when_game_not_running():
    # 不能返回 False：调用方会继续空转重试，等 device 的卡死检测要好几分钟
    with pytest.raises(GameNotRunningError):
        MapMove.map_move_ensure_main_page(FakeMove())
