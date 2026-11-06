# -*- coding: utf-8 -*-
"""
副本 Boss 奖励弹窗回归测试（不连设备）

2026-11-06 02:31 真机：沙狱[单人] 没接 Boss 奖励弹窗规则 —— 沙灵神击杀后
「恭喜您成功击败沙灵神获得以下奖励」一直盖着小地图入口：锁敌点不动「目标」、
连点 7 次小地图也打不开，最后 `Unable to walk to boss [舞娘]` ->
`Run failed for dungeon [沙狱[单人]]`，整轮副本（帝玉岭/琉璃）都没跑。

这里锁住两件事：
1. 全部已接线的副本都能拿到 Boss 奖励弹窗的检测/关闭规则（含沙狱[单人]）；
2. 小地图打不开时先关结算弹窗再重试一次（模态弹窗会吃掉入口点击）。
"""
from types import SimpleNamespace

from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.dungeons.DiyulingMulti import DiyulingMulti
from tasks.Dungeon.dungeons.DiyulingSolo import DiyulingSolo
from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing
from tasks.Dungeon.dungeons.ShayuMulti import ShayuMulti
from tasks.Dungeon.dungeons.ShayuSolo import ShayuSolo
from tasks.WorldBoss.assets import WorldBossAssets

DUNGEONS = (ShayuSolo, ShayuMulti, DiyulingSolo, DiyulingMulti, LiuliHuanjing)


def test_every_dungeon_knows_the_boss_reward_popup():
    for dungeon in DUNGEONS:
        assert dungeon.dungeon_boss_reward_rule(dungeon) is WorldBossAssets.I_BOSS_REWARD
        assert (dungeon.dungeon_boss_reward_close_rule(dungeon)
                is WorldBossAssets.C_BOSS_REWARD_CLOSE)


class FakeMinimap:
    """只覆盖 dungeon_click_minimap_target 会碰到的接口"""

    DUNGEON_PAN_MAX_DRAGS = 10

    def __init__(self, open_results):
        self._open_results = list(open_results)
        self.calls = []
        self.device = SimpleNamespace(
            click_record_clear=lambda: self.calls.append('click_record_clear'),
            sleep=lambda seconds: self.calls.append(('sleep', seconds)),
        )

    def map_open_minimap(self):
        self.calls.append('map_open_minimap')
        return self._open_results.pop(0)

    def dungeon_close_result_popups(self):
        self.calls.append('dungeon_close_result_popups')
        return True

    def map_pan_to_corner(self, corner, max_drags=None):
        self.calls.append(('map_pan_to_corner', corner))
        return True

    def click(self, rule):
        self.calls.append(('click', rule))

    def map_close_minimap(self):
        self.calls.append('map_close_minimap')


def test_minimap_failure_closes_popups_then_retries():
    rule = SimpleNamespace(roi_front=(1, 2, 3, 4))
    fake = FakeMinimap([False, True])

    assert DungeonBase.dungeon_click_minimap_target(fake, 'bottom_left', rule) is True
    assert fake.calls == [
        'map_open_minimap',
        'dungeon_close_result_popups',
        ('sleep', 0.5),
        'map_open_minimap',
        ('map_pan_to_corner', 'bottom_left'),
        ('click', rule),
        'click_record_clear',
        ('sleep', 0.5),
        'map_close_minimap',
    ]


def test_minimap_failure_gives_up_when_popup_close_does_not_help():
    rule = SimpleNamespace(roi_front=(1, 2, 3, 4))
    fake = FakeMinimap([False, False])

    assert DungeonBase.dungeon_click_minimap_target(fake, 'bottom_left', rule) is False
    assert fake.calls.count('map_open_minimap') == 2
    assert 'map_close_minimap' not in fake.calls
