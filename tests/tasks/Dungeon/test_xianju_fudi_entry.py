# -*- coding: utf-8 -*-
"""仙居福地 录入回归测试（不连设备）

2026-11-15 录入：入口在骨牢，万事通 / 副本入口都在骨牢小地图视口**右上角**
（万事通 851,172,24,22；副本入口 797,145,24,22）。
副本内：地图名 仙居福地[副本]、初始坐标 156,273；5 个 Boss 各自在房间里，
流程是「点房间入口进房 -> 等落点坐标 -> 按坐标走到 Boss（map_go_to_coordinate）-> 打」，
最后从左上角出口回骨牢。这里锁住点位表、调用顺序和失败分支。
"""
from types import SimpleNamespace

import pytest

from module.exception import TaskFailed
from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.counts import CONFIG_FIELDS, FIELD_NAMES, config_counts
from tasks.Dungeon.dungeons.XianjuFudi import XianjuFudi
from tasks.Dungeon.runner import DungeonAborted
from tasks.Dungeon.script_task import DUNGEONS


# ---------------------------------------------------------------- 配置 / 登记
def test_dungeon_registered():
    assert DUNGEONS['xianju_fudi_enable'] is XianjuFudi
    # 登记表的 key 去掉 _enable 就是配置字段前缀
    assert XianjuFudi.FIELD == 'xianju_fudi'


def test_config_fields_and_counts_mapping():
    assert CONFIG_FIELDS['仙居福地'] == 'xianju_fudi'
    assert FIELD_NAMES['xianju_fudi'] == '仙居福地'
    # 副本页 OCR 出来的行 -> 配置字段 -> 剩余次数
    assert config_counts({'仙居福地': 2, '沙狱[单人]': 1}) == {
        'xianju_fudi': 2, 'shayu_solo': 1}


def test_config_has_enable_and_count():
    from tasks.Dungeon.config import DungeonConfig, PrivilegeDungeon

    cfg = DungeonConfig()
    assert cfg.xianju_fudi_enable is False        # 副本内流程补完前默认不启用
    assert cfg.xianju_fudi_count == 2
    assert PrivilegeDungeon.XIANJU_FUDI.value == 'xianju_fudi'


# ---------------------------------------------------------------- 外地图 / 万事通
def test_outer_entry_is_gulao_map():
    info = DungeonBase.DUNGEON_OUTER_ENTRIES['xianju_fudi']
    assert info['base'] == '骨牢'
    # 骨牢本身就能传送，不需要再先进一层外地图
    assert info['outer'] is None
    assert info['entry'] is None


def test_healer_point_is_gulao_top_right():
    rule_name, corner = next(
        (rule, corner) for map_name, rule, corner in DungeonBase.DUNGEON_HEALER_MAPS
        if map_name == '骨牢')
    assert rule_name == 'xianju_fudi_healer_npc'
    assert corner == 'top_right'


class FakeDungeon(DungeonBase):
    """不连设备：只用来验证「按地图选万事通点位 / 特征规则表」这类静态查表"""

    def __init__(self):  # 不调父类 __init__（会连设备/OCR）
        pass


def test_healer_target_lookup():
    fake = FakeDungeon()
    rule, corner = fake.dungeon_healer_target('骨牢')
    assert (rule is fake.C_XIANJU_FUDI_HEALER_NPC, corner) == (True, 'top_right')
    rule, corner = fake.dungeon_healer_target('忘川')
    assert (rule is fake.C_LIULI_HEALER_NPC, corner) == (True, 'bottom_left')
    # 没录过点位的图：返回空，交给小地图 NPC 列表兜底
    assert fake.dungeon_healer_target('牧野') == (None, None)


# ---------------------------------------------------------------- 点位素材
def test_click_points_recorded():
    assert XianjuFudi.ENTRY_CLICK_NAME == 'xianju_fudi_entry'
    assert XianjuFudi.ENTRY_CORNER == 'top_right'

    from tasks.Dungeon.assets import DungeonAssets

    # 骨牢小地图（视口右上角）：万事通 / 副本入口
    assert DungeonAssets.C_XIANJU_FUDI_HEALER_NPC.roi_front == (851, 172, 24, 22)
    assert DungeonAssets.C_XIANJU_FUDI_ENTRY.roi_front == (797, 145, 24, 22)


def test_feature_rule_registered():
    """副本页选中校验用的特征图已经录好（I_XIANJU_FEATURE）"""
    fake = FakeDungeon()
    names = [name for _, name in fake.dungeon_feature_rules()]
    assert '仙居福地' in names
    assert fake.I_XIANJU_FEATURE is not None


# ---------------------------------------------------------------- 副本内：5 个房间
def test_five_rooms_recorded():
    heroes = [room[0] for room in XianjuFudi.ROOMS]
    assert heroes == ['焱', '垚', '森', '淼', '鑫']
    for boss, rule_name, room_position, boss_position in XianjuFudi.ROOMS:
        assert rule_name.startswith('xianju_fudi_room_')
        assert len(room_position) == 2 and len(boss_position) == 2
        # 生僻字要登记 OCR 别名，否则「锁到了但名字对不上」会一直换目标
        assert boss in XianjuFudi.BOSS_NAME_ALIASES


def test_room_and_exit_points_recorded():
    from tasks.Dungeon.assets import DungeonAssets

    assert DungeonAssets.C_XIANJU_FUDI_ROOM_1.roi_front == (388, 508, 30, 27)
    assert DungeonAssets.C_XIANJU_FUDI_ROOM_2.roi_front == (574, 573, 30, 27)
    assert DungeonAssets.C_XIANJU_FUDI_ROOM_3.roi_front == (553, 364, 30, 27)
    assert DungeonAssets.C_XIANJU_FUDI_ROOM_4.roi_front == (720, 476, 28, 23)
    assert DungeonAssets.C_XIANJU_FUDI_ROOM_5.roi_front == (709, 363, 28, 23)
    assert DungeonAssets.C_XIANJU_FUDI_EXIT.roi_front == (812, 198, 28, 23)


def test_map_name_and_calibration_name():
    # 地点 OCR 的名字不带括号，maps.json 也按「仙居福地副本」录
    assert XianjuFudi.MAP_CALIBRATION_NAME == '仙居福地副本'
    assert FakeDungeon().map_name_match('仙居福地[副本]', '仙居福地副本') is True
    assert FakeDungeon().map_name_match('仙居福地副本', '仙居福地[副本]') is True


class FakeInsideDungeon(XianjuFudi):
    """只测 run_from_inside 的调用顺序，不碰设备（常量/点位沿用真类）"""

    def __init__(self, room_entry_ok=True, fight_results=None, dead_after=None):
        self.calls = []
        self.room_entry_ok = room_entry_ok
        self.fight_results = list(fight_results or [])
        # None / 'go_to' / 'fight'：走到哪一步「死亡」（复活后回骨牢）
        self.dead_after = dead_after
        self.dead = False
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def dungeon_left_for_death(self):
        return self.dead

    def finish_run_after_death(self):
        self.calls.append(('finish_after_death',))
        self.dead = False
        return True

    def dungeon_check_in_map(self, map_name):
        self.calls.append(('check_in_map', map_name))

    def dungeon_finish_detected(self, image=None):
        return False

    def dungeon_close_boss_reward(self):
        self.calls.append(('close_reward',))

    def dungeon_minimap_walk(self, corner, click_rule, target_map, position=None,
                             tolerance=None, timeout=None, abort_on_leave=False,
                             retries=1):
        self.calls.append(('minimap_walk', corner, click_rule.name, target_map,
                           position, tolerance))
        return self.room_entry_ok

    def dungeon_go_to_position(self, label, position, map_name=None, timeout=90,
                               tolerance=3, lock_targets=None):
        self.calls.append(('go_to_position', label, position, map_name, tolerance,
                           tuple(lock_targets or ())))
        if self.dead_after == 'go_to':
            self.dead = True
            return False
        return True

    def dungeon_fight_boss(self, target_name, timeout=None, map_name=None):
        self.calls.append(('fight', target_name, map_name))
        if self.dead_after == 'fight':
            self.dead = True
            return 'killed'
        return self.fight_results.pop(0) if self.fight_results else 'killed'

    def dungeon_close_result_popups(self):
        self.calls.append(('close_result',))

    def dungeon_wait_auto_leave(self, dungeon_map, timeout=None):
        self.calls.append(('auto_leave', dungeon_map))
        return True


def test_run_from_inside_visits_all_five_rooms_then_exits():
    fake = FakeInsideDungeon()
    assert XianjuFudi.run_from_inside(fake) is True

    room_calls = [call for call in fake.calls if call[0] == 'minimap_walk']
    assert room_calls[0] == ('minimap_walk', 'top_left', 'xianju_fudi_room_1',
                             '仙居福地[副本]', (142, 243),
                             XianjuFudi.ROOM_ARRIVE_TOLERANCE)
    # 出口：左上角 -> 骨牢
    assert room_calls[-1] == ('minimap_walk', 'top_left', 'xianju_fudi_exit',
                              '骨牢', None, None)
    assert len(room_calls) == 6                       # 5 个房间 + 1 个出口

    # 每个房间：先进房 -> 再按坐标走到 Boss -> 再打
    orders = [call[0] for call in fake.calls
              if call[0] in ('minimap_walk', 'go_to_position', 'fight')]
    assert orders[:6] == ['minimap_walk', 'go_to_position', 'fight',
                          'minimap_walk', 'go_to_position', 'fight']
    go_to = [call for call in fake.calls if call[0] == 'go_to_position']
    # 走位时带上锁敌白名单（含生僻字别名）
    assert go_to[0] == ('go_to_position', '焱', (63, 190), '仙居福地副本',
                        XianjuFudi.BOSS_POSITION_TOLERANCE, ('焱', '炎'))
    assert [call[1] for call in go_to] == ['焱', '垚', '森', '淼', '鑫']
    assert [call[2] for call in go_to] == [(63, 190), (331, 373), (163, 89),
                                           (440, 273), (419, 85)]


def test_run_from_inside_stops_when_room_entry_fails():
    fake = FakeInsideDungeon(room_entry_ok=False)
    assert XianjuFudi.run_from_inside(fake) is False
    assert not [call for call in fake.calls if call[0] == 'fight']


def test_run_from_inside_stops_when_boss_fight_fails():
    fake = FakeInsideDungeon(fight_results=['timeout'])
    assert XianjuFudi.run_from_inside(fake) is False
    assert len([call for call in fake.calls if call[0] == 'fight']) == 1


# ---------------------------------------------------------------- 坐标走路（基类 helper）
class FakeGoToPosition:
    name = '仙居福地'

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def map_go_to_coordinate(self, x, y, **kwargs):
        self.calls.append((x, y, kwargs))
        if self.error is not None:
            raise self.error
        return '仙居福地副本', x, y


def test_dungeon_go_to_position_returns_true_on_success():
    fake = FakeGoToPosition()
    assert DungeonBase.dungeon_go_to_position(
        fake, '焱', (63, 190), map_name='仙居福地副本', tolerance=3) is True
    assert fake.calls == [(63, 190, {'map_name': '仙居福地副本',
                                     'timeout': 90, 'tolerance': 3,
                                     'on_poll': None})]


def test_dungeon_go_to_position_passes_lock_callback():
    """走位时传 on_poll：途中锁到目标就提前结束寻路"""
    fake = FakeGoToPosition()
    fake.locked = False

    def try_lock(targets):
        fake.calls.append(('try_lock', tuple(targets)))
        return fake.locked

    fake.dungeon_try_lock_target = try_lock
    assert DungeonBase.dungeon_go_to_position(
        fake, '焱', (63, 190), map_name='仙居福地副本',
        lock_targets=['焱', '炎']) is True
    on_poll = fake.calls[0][2]['on_poll']
    assert callable(on_poll)
    assert on_poll(('仙居福地副本', 100, 100)) is False
    fake.locked = True
    assert on_poll(('仙居福地副本', 110, 100)) is True
    assert ('try_lock', ('焱', '炎')) in fake.calls


def test_dungeon_go_to_position_returns_false_on_task_failed():
    fake = FakeGoToPosition(TaskFailed('坐标不可达'))
    assert DungeonBase.dungeon_go_to_position(
        fake, '焱', (63, 190), map_name='仙居福地副本') is False


# ---------------------------------------------------------------- 副本内死亡
class FakeLocationDungeon(XianjuFudi):
    """只测「人已不在副本内」的判定，不碰设备"""

    def __init__(self, location):
        self.location = location
        self._died_this_run = False

    def map_current_location(self, image=None):
        return self.location


def test_dungeon_check_in_map_marks_death_instead_of_aborting():
    """副本内死亡不会推「未通关」/ 不会抛 DungeonAborted，只打标记（这局算完成一次）"""
    fake = FakeLocationDungeon(('骨牢', 163, 480))
    XianjuFudi.dungeon_check_in_map(fake, XianjuFudi.MAP_NAME)   # 不抛异常
    assert fake._died_this_run is True


def test_dungeon_left_for_death_false_when_still_in_dungeon():
    fake = FakeLocationDungeon(('仙居福地副本', 142, 243))
    assert XianjuFudi.dungeon_left_for_death(fake) is False
    assert fake._died_this_run is False
    # 地点读不出来时不误判成死亡
    fake = FakeLocationDungeon(None)
    assert XianjuFudi.dungeon_left_for_death(fake) is False


def test_run_from_inside_finishes_run_when_died_while_walking():
    fake = FakeInsideDungeon(dead_after='go_to')
    assert XianjuFudi.run_from_inside(fake) is True
    # 死了就不再打、也不用从出口离场（人已经在骨牢了）
    assert not [call for call in fake.calls if call[0] == 'fight']
    assert not [call for call in fake.calls if call[0] == 'minimap_walk'
                and call[2] == 'xianju_fudi_exit']
    assert ('finish_after_death',) in fake.calls


def test_run_from_inside_finishes_run_when_died_while_fighting():
    fake = FakeInsideDungeon(dead_after='fight')
    assert XianjuFudi.run_from_inside(fake) is True
    # 只打了第一只就收尾，不再进下一个房间
    assert len([call for call in fake.calls if call[0] == 'fight']) == 1
    assert len([call for call in fake.calls if call[0] == 'minimap_walk']) == 1


class FakeDeathDialog(XianjuFudi):
    """只测 handle_death：死亡框出现 -> 复活"""

    def __init__(self):
        self.calls = []

    def appear(self, target, interval=None):
        return True

    def revive(self, retry=3, timeout=30):
        self.calls.append('revive')
        return True

    def handle_death_resume(self):
        self.calls.append('resume')


def test_handle_death_revives_and_resumes():
    fake = FakeDeathDialog()
    XianjuFudi.handle_death(fake)
    assert fake.calls == ['revive', 'resume']


# ---------------------------------------------------------------- 副本前准备顺序
class FakePrepareOrder(XianjuFudi):
    """只测 run_once 的准备顺序：先进骨牢 -> 修理装备 -> 万事通治疗 -> 进副本"""

    def __init__(self):
        self.calls = []

    def dungeon_enter_outer(self, field):
        self.calls.append('enter_outer')
        return True

    def dungeon_repair_equipment(self):
        self.calls.append('repair')
        return True

    def dungeon_heal(self):
        self.calls.append('heal')
        return True

    def dungeon_minimap_walk(self, corner, click_rule, target_map, position=None,
                             tolerance=None, timeout=None, abort_on_leave=False,
                             retries=1):
        self.calls.append('enter_dungeon')
        return True

    def run_from_inside(self):
        self.calls.append('inside')
        return True


def test_run_once_repairs_before_healing():
    task = FakePrepareOrder()
    assert XianjuFudi.run_once(task) is True
    assert task.calls == ['enter_outer', 'repair', 'heal', 'enter_dungeon', 'inside']
