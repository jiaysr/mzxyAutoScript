# -*- coding: utf-8 -*-
"""副本战斗检查降频与截图复用回归测试（不连设备）"""
from types import SimpleNamespace

from tasks.Battle.target_action import ACTION_ATTACK
from tasks.Dungeon import common as dungeon_common
from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.dungeons.XianjuFudi import XianjuFudi


def _battle_stop_fake(frame, **overrides):
    values = dict(
        _dungeon_fight_deadline=1000.0,
        device=SimpleNamespace(image=frame),
        exist_image=lambda: True,
        dungeon_finish_visible=lambda image: False,
        dungeon_boss_reward_visible=lambda image: False,
        _dungeon_fight_map=None,
        _dungeon_next_map_check=1000.0,
        DUNGEON_MAP_CHECK_INTERVAL=3,
        ui_target_locked=lambda image: True,
        ui_target_action=lambda image: ACTION_ATTACK,
        ui_target_name=lambda **kwargs: 'Boss',
        ocr_name_match=lambda text, expected: text == expected,
        _dungeon_fight_target=['Boss'],
        _dungeon_next_target_name_check=0.0,
        DUNGEON_TARGET_NAME_CHECK_INTERVAL=1.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dungeon_check_in_map_passes_current_frame_to_location_reader():
    seen = []
    fake = SimpleNamespace(
        map_current_location=lambda image=None: seen.append(image),
    )
    frame = object()

    DungeonBase.dungeon_check_in_map(fake, '副本地图', image=frame)

    assert seen == [frame]


def test_xianju_fudi_map_checks_reuse_current_frame():
    seen = []
    frame = object()
    fake = SimpleNamespace(
        _died_this_run=False,
        map_current_location=lambda image=None: seen.append(image),
        dungeon_left_for_death=lambda image=None: seen.append(image),
    )

    assert XianjuFudi.dungeon_left_for_death(fake, image=frame) is False
    XianjuFudi.dungeon_check_in_map(fake, XianjuFudi.MAP_NAME, image=frame)

    assert seen == [frame, frame]


def test_battle_map_check_reuses_loop_frame(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(dungeon_common.time, 'time', lambda: now[0])
    checked = []
    frame = object()
    fake = _battle_stop_fake(
        frame,
        _dungeon_fight_map='副本地图',
        _dungeon_next_map_check=0.0,
        _dungeon_next_target_name_check=1000.0,
        dungeon_check_in_map=lambda map_name, image=None: checked.append(
            (map_name, image)),
    )

    assert DungeonBase._dungeon_battle_stop(fake) is False
    assert checked == [('副本地图', frame)]


def test_target_name_ocr_runs_at_most_once_per_interval(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(dungeon_common.time, 'time', lambda: now[0])
    ocr_calls = []
    current_name = ['Boss']

    def read_name(**kwargs):
        ocr_calls.append(kwargs)
        return current_name[0]

    fake = _battle_stop_fake(object(), ui_target_name=read_name)

    # 进入战斗后立即识别一次
    assert DungeonBase._dungeon_battle_stop(fake) is False
    assert ocr_calls == [{}]

    # 间隔内的后续技能轮次不再 OCR
    now[0] = 100.5
    assert DungeonBase._dungeon_battle_stop(fake) is False
    assert len(ocr_calls) == 1

    # 到达 1 秒间隔后允许再次识别
    now[0] = 101.0
    assert DungeonBase._dungeon_battle_stop(fake) is False
    assert len(ocr_calls) == 2

    # 降频不改变名字不匹配时停止攻击的行为
    current_name[0] = 'OtherBoss'
    now[0] = 102.0
    assert DungeonBase._dungeon_battle_stop(fake) is True
    assert len(ocr_calls) == 3
