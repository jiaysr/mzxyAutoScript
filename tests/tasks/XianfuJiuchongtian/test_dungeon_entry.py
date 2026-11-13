# -*- coding: utf-8 -*-
"""仙府九重天-进图判定/副本内续跑测试（不连设备）

背景：副本 HUD 和主页面一样（没有侧栏、右上角只有地点文字、左上角多一个倒计时），
页面引擎会把副本画面判成 page_main。上次运行失败后人就留在副本里，
下次运行按正常流程导航只会乱点直到超时。
"""
from types import SimpleNamespace

import pytest

from module.exception import TaskEnd
from tasks.XianfuJiuchongtian.script_task import ScriptTask


class FakeLoadTask:
    """只测 wait_dungeon_loaded：第 N 帧后出现「已进图」信号"""

    DUNGEON_LOAD_TIMEOUT = 25
    I_DUNGEON_COUNTDOWN = 'countdown'

    def __init__(self, appear_after=1, by_countdown=True):
        self.appear_after = appear_after
        self.by_countdown = by_countdown
        self.shots = 0
        self.device = SimpleNamespace(image=object(), sleep=lambda seconds: None)

    def reset_records(self):
        pass

    def screenshot(self):
        self.shots += 1
        return self.device.image

    def appear(self, target, interval=None):
        return self.by_countdown and self.shots >= self.appear_after

    def in_xianfu_map(self, image=None):
        return (not self.by_countdown) and self.shots >= self.appear_after


def test_wait_dungeon_loaded_accepts_countdown():
    """地点文字读不出来（返回 None）时，左上角倒计时也能证明已进图"""
    task = FakeLoadTask(appear_after=2, by_countdown=True)
    assert ScriptTask.wait_dungeon_loaded(task, timeout=5) is True
    assert task.shots >= 2


def test_wait_dungeon_loaded_accepts_location_text():
    task = FakeLoadTask(appear_after=2, by_countdown=False)
    assert ScriptTask.wait_dungeon_loaded(task, timeout=5) is True


def test_wait_dungeon_loaded_times_out():
    task = FakeLoadTask(appear_after=10 ** 6)
    assert ScriptTask.wait_dungeon_loaded(task, timeout=0.2) is False


class FakeBattleEntryTask(FakeLoadTask):
    """只测 battle() 的进图重试点「开始挑战」那段"""

    DUNGEON_LOAD_TIMEOUT = 25
    DUNGEON_LOAD_RETRY = 2
    BATTLE_MOVE_RETRY = 1
    I_XIANFU_PAGE = 'xianfu_page'

    def __init__(self, load_results, on_xianfu_page=True):
        super().__init__()
        self.load_results = list(load_results)
        self.on_xianfu_page = on_xianfu_page
        self.start_clicks = 0

    def wait_dungeon_loaded(self, timeout=None):
        return self.load_results.pop(0)

    def appear(self, target, interval=None):
        return self.on_xianfu_page

    def start_challenge(self, timeout=15):
        self.start_clicks += 1
        return True

    def map_open_minimap(self):
        return False

    def map_close_main_popup(self):
        pass


def test_battle_retries_start_challenge_once_then_fights():
    # 第一次没进图 -> 重点一次「开始挑战」-> 这次进图了，不再重点
    task = FakeBattleEntryTask(load_results=[False, True])
    assert ScriptTask.battle(task) == 'failed'   # 小地图打开失败->战斗失败
    assert task.start_clicks == 1


def test_battle_retries_until_limit_then_gives_up():
    task = FakeBattleEntryTask(load_results=[False, False, False])
    assert ScriptTask.battle(task) == 'failed'
    assert task.start_clicks == ScriptTask.DUNGEON_LOAD_RETRY == 2


def test_battle_gives_up_when_page_is_gone():
    task = FakeBattleEntryTask(load_results=[False], on_xianfu_page=False)
    assert ScriptTask.battle(task) == 'failed'
    assert task.start_clicks == 0


class FakeResumeTask:
    I_REWARD_PANEL = 'reward_panel'

    def __init__(self, in_map=True, reward_panel_shown=False, battle_result='reward'):
        self.in_map = in_map
        self.reward_panel_shown = reward_panel_shown
        self.battle_result = battle_result
        self.battled = 0
        self.claimed = 0
        self.waited = 0

    def in_xianfu_map(self, image=None):
        return self.in_map

    def appear(self, target, interval=None):
        return self.reward_panel_shown

    def battle(self):
        self.battled += 1
        return self.battle_result

    def wait_stage_result(self):
        self.waited += 1
        return 'failed'

    def claim_reward(self):
        self.claimed += 1
        return True


def test_resume_skips_when_not_in_dungeon():
    task = FakeResumeTask(in_map=False)
    assert ScriptTask.finish_pending_battle(task) is False
    assert task.battled == 0
    assert task.claimed == 0


def test_resume_battles_and_claims_when_in_dungeon():
    task = FakeResumeTask(in_map=True, battle_result='reward')
    assert ScriptTask.finish_pending_battle(task) is True
    assert task.battled == 1
    assert task.claimed == 1


def test_resume_claims_directly_when_reward_panel_shown():
    task = FakeResumeTask(in_map=True, reward_panel_shown=True)
    assert ScriptTask.finish_pending_battle(task) is True
    assert task.battled == 0
    assert task.claimed == 1


def test_resume_does_not_claim_when_battle_failed():
    task = FakeResumeTask(in_map=True, battle_result='failed')
    assert ScriptTask.finish_pending_battle(task) is True
    assert task.battled == 1
    assert task.claimed == 0


class FakeRunTask:
    """run() 只验证顺序：副本内续跑必须排在读仙石/背包之前"""

    def __init__(self):
        self.calls = []
        self.config = SimpleNamespace(
            notifier=SimpleNamespace(push=lambda **kwargs: None))

    def load_config(self):
        self.calls.append('load_config')
        return 3, 20, 0, 0

    def finish_pending_battle(self):
        self.calls.append('finish_pending_battle')
        return False

    def prepare(self, threshold, color, fixed_daguan):
        self.calls.append('prepare')
        return 21, {1: 0, 2: 0, 3: 0, 4: 0}

    def repair_before_battle(self):
        self.calls.append('repair')

    def pick_target(self, counts):
        return 4

    def run_one_battle(self, daguan, xiaoguan):
        self.calls.append('battle')
        return False

    def read_stone_ready(self):
        return 0

    def schedule_next_run(self, stone, threshold):
        self.calls.append('schedule')

    def set_next_run(self, **kwargs):
        pass

    def notify_stage_failed(self, daguan, xiaoguan):
        pass


def test_run_finishes_pending_battle_before_prepare():
    task = FakeRunTask()
    with pytest.raises(TaskEnd):
        ScriptTask.run(task)
    assert task.calls == ['load_config', 'finish_pending_battle',
                          'prepare', 'repair', 'battle', 'schedule']
