# -*- coding: utf-8 -*-
"""天师的幻影 接入回归测试（不连设备）

2026-11-16 新增：天师的幻影不在活动页的首领列表里（没有「寻」），
直接进世界之巅地图按坐标走过去（252,241）；每天 12:00 / 22:00 刷新。
世界之巅**没有传送出口**：进图靠「进入世界之巅」活动按钮，离开只能重启游戏，
所以：
- 只勾了天师：打完（结束消息或超时）-> 重启离开 -> 任务结束；
- 同一时段勾了两个 boss：顺序写死「天师先打」-> 打够技能数 -> 重启离开 -> 打另一个。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from module.exception import TaskFailed
from tasks.WorldBoss.config import TianshiConfig, WorldBoss
from tasks.WorldBoss.script_task import (BOSS_LIST, NAVIGATE_RETRY,
                                         REACH_WORLD_PEAK, ScriptTask)

TIANSHI = next(boss for boss in BOSS_LIST if boss['key'] == 'tianshi')
WUQING = next(boss for boss in BOSS_LIST if boss['key'] == 'wuqing')
FUWANG = next(boss for boss in BOSS_LIST if boss['key'] == 'fuwang')
JULIAN = next(boss for boss in BOSS_LIST if boss['key'] == 'julian')


def fake_config(first_boss='auto', first_boss_skills=3, tianshi_12=True,
                tianshi_22=False):
    config = WorldBoss()
    config.world_boss_config.first_boss = first_boss
    config.world_boss_config.first_boss_skills = first_boss_skills
    config.tianshi = TianshiConfig(time_12=tianshi_12, time_22=tianshi_22)
    return SimpleNamespace(world_boss=config)


# ---------------------------------------------------------------- 数据/配置
def test_boss_registered_as_direct_reach():
    assert TIANSHI['name'] == '天师的幻影'
    assert TIANSHI['target_map'] == '世界之巅'
    assert TIANSHI['target_coord'] == (252, 241)
    assert TIANSHI['reach'] == REACH_WORLD_PEAK
    # 12:00 / 22:00 两场
    assert [field for field, _ in TIANSHI['times']] == ['time_12', 'time_22']
    assert [event_time.hour for _, event_time in TIANSHI['times']] == [12, 22]
    assert ScriptTask.boss_direct_reach(None, TIANSHI) is True
    assert ScriptTask.boss_direct_reach(None, WUQING) is False


def test_config_group_and_enabled_events():
    config = WorldBoss()
    assert config.tianshi.time_12 is False
    assert config.tianshi.time_22 is False

    fake = SimpleNamespace(config=fake_config(tianshi_12=True, tianshi_22=True))
    keys = [boss['key'] for boss, _ in ScriptTask.iter_enabled_events(fake)]
    assert keys.count('tianshi') == 2


def test_finish_asset_recorded():
    from tasks.WorldBoss.assets import WorldBossAssets

    assert WorldBossAssets.I_FINISH_TIANSHI.name == 'RES_FINISH_TIANSHI'
    # finish_rule 按 key 取素材：天师 -> I_FINISH_TIANSHI
    assert ScriptTask.finish_rule(WorldBossAssets, TIANSHI) is WorldBossAssets.I_FINISH_TIANSHI
    assert ScriptTask.finish_rule(WorldBossAssets, WUQING) is WorldBossAssets.I_FINISH_WUQING


# ---------------------------------------------------------------- 攻击顺序
def test_pair_order_hardcodes_tianshi_first():
    fake = SimpleNamespace(config=fake_config(first_boss='wuqing'),
                           boss_direct_reach=lambda boss: ScriptTask.boss_direct_reach(None, boss))
    first, second = ScriptTask.pair_order(fake, [WUQING, TIANSHI])
    assert first['key'] == 'tianshi'
    assert second['key'] == 'wuqing'

    fake = SimpleNamespace(config=fake_config(first_boss='fuwang'),
                           boss_direct_reach=lambda boss: ScriptTask.boss_direct_reach(None, boss))
    first, second = ScriptTask.pair_order(fake, [TIANSHI, FUWANG])
    assert first['key'] == 'tianshi'
    assert second['key'] == 'fuwang'


def test_pair_order_keeps_config_without_tianshi():
    fake = SimpleNamespace(config=fake_config(first_boss='julian'),
                           boss_direct_reach=lambda boss: ScriptTask.boss_direct_reach(None, boss))
    first, second = ScriptTask.pair_order(fake, [WUQING, JULIAN])
    assert first['key'] == 'julian'


# ---------------------------------------------------------------- 前往
class FakeNavigate(ScriptTask):
    """只测 navigate_to_boss 的分派，不碰设备"""

    def __init__(self, enter_world_peak_ok=True, results=None):
        self.calls = []
        self.config = fake_config()
        self.enter_world_peak_ok = enter_world_peak_ok
        self.results = list(results or [])
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def map_in_location(self, name):
        return False          # 不在图里 -> 走 enter_world_peak_map

    def enter_world_peak_map(self):
        self.calls.append('enter_world_peak')
        return self.enter_world_peak_ok

    def walk_to_direct_boss(self, boss, finish_rule, timeout=180):
        self.calls.append(('walk_direct', boss['key']))
        return 'arrived'

    def prepare_boss_map(self, boss):
        self.calls.append(('prepare_map', boss['key']))
        return True

    def enter_world_boss_page(self, timeout=40):
        self.calls.append('enter_boss_page')
        return True

    def seek_boss(self, boss):
        self.calls.append(('seek', boss['key']))
        return True

    def wait_until_arrive(self, boss, timeout=180, finish_rule=None):
        self.calls.append(('wait_arrive', boss['key']))
        return 'arrived'


def test_navigate_direct_boss_uses_world_peak():
    fake = FakeNavigate()
    deadline = datetime.now() + timedelta(minutes=5)
    assert ScriptTask.navigate_to_boss(fake, TIANSHI, None, deadline) == 'arrived'
    # 不进活动页、不点「寻」，只进世界之巅 + 按坐标走
    assert fake.calls == ['enter_world_peak', ('walk_direct', 'tianshi')]


def test_navigate_normal_boss_uses_activity_page():
    fake = FakeNavigate()
    deadline = datetime.now() + timedelta(minutes=5)
    assert ScriptTask.navigate_to_boss(fake, WUQING, None, deadline) == 'arrived'
    assert fake.calls == [('prepare_map', 'wuqing'), 'enter_boss_page',
                          ('seek', 'wuqing'), ('wait_arrive', 'wuqing')]


def test_navigate_fails_when_world_peak_unreachable():
    fake = FakeNavigate(enter_world_peak_ok=False)
    deadline = datetime.now() + timedelta(minutes=5)
    assert ScriptTask.navigate_to_boss(fake, TIANSHI, None, deadline) == 'failed'
    # 重试 NAVIGATE_RETRY 次，但一次都没走
    assert fake.calls == ['enter_world_peak'] * (NAVIGATE_RETRY + 1)
    assert not [call for call in fake.calls
                if isinstance(call, tuple) and call[0] == 'walk_direct']


def test_navigate_retries_after_death_reset():
    """死亡复活后被送回世界之巅初始位置：走位失败要重试，直到走回 Boss 坐标"""
    attempts = {'count': 0}

    class Fake(FakeNavigate):
        def walk_to_direct_boss(self, boss, finish_rule, timeout=180):
            attempts['count'] += 1
            self.calls.append(('walk_direct', boss['key'], attempts['count']))
            return 'timeout' if attempts['count'] == 1 else 'arrived'

    fake = Fake()
    deadline = datetime.now() + timedelta(minutes=5)
    assert ScriptTask.navigate_to_boss(fake, TIANSHI, None, deadline) == 'arrived'
    assert attempts['count'] == 2


class FakeWalk(ScriptTask):
    """只测 walk_to_direct_boss：地图坐标寻路 + 途中查结束消息"""

    def __init__(self, error=None, finish_in_walk=False):
        self.error = error
        self.finish_in_walk = finish_in_walk
        self.kwargs = None
        self.device = SimpleNamespace(image=object())

    def exist_image(self):
        return True

    def screenshot(self):
        return self.device.image

    def finish_confirmed(self, boss, rule, image):
        return self.finish_in_walk

    def map_go_to_coordinate(self, x, y, **kwargs):
        self.kwargs = (x, y, kwargs)
        on_poll = kwargs.get('on_poll')
        if on_poll is not None:
            on_poll(('世界之巅', x, y))
        if self.error is not None:
            raise self.error
        return '世界之巅', x, y


def test_walk_to_direct_boss_uses_calibrated_map():
    fake = FakeWalk()
    assert ScriptTask.walk_to_direct_boss(fake, TIANSHI, None) == 'arrived'
    x, y, kwargs = fake.kwargs
    assert (x, y) == (252, 241)
    assert kwargs['map_name'] == '世界之巅'
    assert callable(kwargs['on_poll'])


def test_walk_to_direct_boss_returns_finished_when_template_hits():
    fake = FakeWalk(finish_in_walk=True)
    assert ScriptTask.walk_to_direct_boss(fake, TIANSHI, object()) == 'finished'


def test_walk_to_direct_boss_returns_timeout_on_failure():
    fake = FakeWalk(error=TaskFailed('坐标不可达'))
    assert ScriptTask.walk_to_direct_boss(fake, TIANSHI, None) == 'timeout'


# ---------------------------------------------------------------- 图内重进/读位置
class FakePeakMap(ScriptTask):
    """只测 ensure_world_peak_in_map：复活/读条期间地点短暂读不出来不能误判离开"""

    def __init__(self, reads, enter_ok=True):
        self.reads = list(reads)
        self.enter_ok = enter_ok
        self.enter_calls = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def map_in_location(self, name):
        return self.reads.pop(0) if self.reads else False

    def enter_world_peak_map(self):
        self.enter_calls += 1
        return self.enter_ok


def test_ensure_world_peak_in_map_retries_reading_location():
    # 前两次读不到（复活读条中），第三次读到 -> 当成还在图里，不重新进图
    fake = FakePeakMap([False, False, True])
    assert ScriptTask.ensure_world_peak_in_map(fake) is True
    assert fake.enter_calls == 0


def test_ensure_world_peak_in_map_reenters_when_really_outside():
    fake = FakePeakMap([False, False, False])
    assert ScriptTask.ensure_world_peak_in_map(fake) is True
    assert fake.enter_calls == 1


def test_ensure_world_peak_in_map_fails_when_cannot_reenter():
    fake = FakePeakMap([False, False, False], enter_ok=False)
    assert ScriptTask.ensure_world_peak_in_map(fake) is False


# ---------------------------------------------------------------- 离开世界之巅
class FakeLeave(ScriptTask):
    def __init__(self, in_map=True, restart_ok=True):
        self.in_map = in_map
        self.restart_ok = restart_ok
        self.calls = []
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def map_in_location(self, name):
        self.calls.append(('check_map', name))
        return self.in_map

    def world_peak_task(self):
        outer = self

        class T:
            def restart_and_wait(self, timeout=120):
                outer.calls.append('restart')
                return outer.restart_ok

        return T()


def test_leave_world_peak_restarts_only_when_inside():
    outside = FakeLeave(in_map=False)
    assert ScriptTask.leave_world_peak_if_needed(outside, 'test') is True
    assert 'restart' not in outside.calls

    inside = FakeLeave(in_map=True)
    assert ScriptTask.leave_world_peak_if_needed(inside, 'test') is True
    assert inside.calls == [('check_map', '世界之巅'), 'restart']


def test_leave_world_peak_reports_restart_failure():
    inside = FakeLeave(in_map=True, restart_ok=False)
    assert ScriptTask.leave_world_peak_if_needed(inside) is False


# ---------------------------------------------------------------- 双 boss 流程
class FakePair(ScriptTask):
    """只测 simultaneous_flow 的顺序：天师打够技能数 -> 重启 -> 另一个 boss"""

    def __init__(self):
        self.config = fake_config(first_boss='wuqing', first_boss_skills=3,
                                  tianshi_12=True)
        self.calls = []
        self.done_events = set()
        self._death_stage = None
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def pair_fight_boss(self, boss, pair, state, event_time, deadline,
                        max_skills=None, lock_timeout=None, cast_counter=None):
        self.calls.append(('fight', boss['key'], max_skills))
        if cast_counter is not None:
            cast_counter['casts'] = max_skills or 2
        if max_skills:
            return 'skill_limit'
        # 打到结束：真实现是结束模板把 state 置 True
        state[boss['key']] = True
        return 'finished'

    def leave_world_peak_if_needed(self, reason=''):
        self.calls.append(('leave', reason))
        return True


def test_simultaneous_flow_tianshi_first_then_restart_then_other():
    fake = FakePair()
    event_time = datetime.now() - timedelta(seconds=1)
    ScriptTask.simultaneous_flow(fake, [WUQING, TIANSHI], event_time)

    fights = [call for call in fake.calls if call[0] == 'fight']
    # 天师先打且带技能数限制，之后重启离开，再打无情
    assert fights[0] == ('fight', 'tianshi', 3)
    assert any(call[0] == 'leave' for call in fake.calls)
    assert ('fight', 'wuqing', None) in fights
    # 重启后不再回世界之巅打天师
    assert [call for call in fights if call[1] == 'tianshi'] == [('fight', 'tianshi', 3)]
    # 天师按「已结束」记进 done_events，不会在收尾时被当成未完成
    assert any('tianshi' in key for key in fake.done_events)


def test_simultaneous_flow_keep_pair_order_without_tianshi():
    fake = FakePair()
    event_time = datetime.now() - timedelta(seconds=1)
    ScriptTask.simultaneous_flow(fake, [WUQING, JULIAN], event_time)
    fights = [call for call in fake.calls if call[0] == 'fight']
    # 没勾天师时按配置（first_boss=wuqing）+ 技能数，且不需要重启
    assert fights[0] == ('fight', 'wuqing', 3)
    assert not any(call[0] == 'leave' for call in fake.calls)
