# -*- coding: utf-8 -*-
"""伏魔山涧任务方式回归测试（不连设备）"""
from types import SimpleNamespace

import pytest

from module.exception import GameStuckError, TaskEnd
from tasks.FumoShanjian.config import FumoShanjian, FumoTaskMode
from tasks.FumoShanjian.script_task import DeathResume, ScriptTask


# ---------------------------------------------------------------- 配置
def test_default_task_mode_is_active_only():
    config = FumoShanjian().fumo_shanjian_config
    assert config.task_mode == FumoTaskMode.ACTIVE_ONLY


def test_task_mode_options_order():
    # 前端下拉框按枚举定义顺序展示：先「一直继续」，再「只完成活跃任务」
    assert [mode.value for mode in FumoTaskMode] == ['always', 'active_only']


def test_config_accepts_both_modes():
    assert FumoShanjian(**{'fumo_shanjian_config': {'task_mode': 'always'}}
                        ).fumo_shanjian_config.task_mode == FumoTaskMode.ALWAYS
    assert FumoShanjian(**{'fumo_shanjian_config': {'task_mode': 'active_only'}}
                        ).fumo_shanjian_config.task_mode == FumoTaskMode.ACTIVE_ONLY


# ---------------------------------------------------------------- 活跃度检查
class FakeTask:
    """只测 active_task_finished 的分派，不碰设备"""

    ACTIVE_TASK_NAME = '伏魔山涧'

    def __init__(self, mode, completed=False, raises=None):
        self.config = SimpleNamespace(
            fumo_shanjian=SimpleNamespace(
                fumo_shanjian_config=SimpleNamespace(task_mode=mode)))
        self.completed = completed
        self.raises = raises
        self.checked = []
        self.reset_page = 0
        self.goto_main = 0
        self.restarted = 0

    def active_task_completed(self, name):
        self.checked.append(name)
        if self.raises is not None:
            raise self.raises
        return self.completed

    def ui_reset_current_page(self):
        self.reset_page += 1

    def ui_goto(self, page, timeout=60):
        self.goto_main += 1
        return True

    def ui_restart_game(self):
        self.restarted += 1

    def back_to_main_from_activity(self):
        self.reset_page += 1
        if not self.ui_goto(None, timeout=60):
            self.ui_restart_game()


def test_active_mode_completed_finishes_without_dungeon():
    fake = FakeTask(FumoTaskMode.ACTIVE_ONLY, completed=True)
    assert ScriptTask.active_task_finished(fake) is True
    assert fake.checked == ['伏魔山涧']
    # 已完成直接收工，不需要回主页面
    assert fake.goto_main == 0


def test_active_mode_not_completed_returns_to_main_page():
    fake = FakeTask(FumoTaskMode.ACTIVE_ONLY, completed=False)
    assert ScriptTask.active_task_finished(fake) is False
    assert fake.checked == ['伏魔山涧']
    assert fake.reset_page == 1
    assert fake.goto_main == 1


def test_always_mode_skips_activity_check():
    fake = FakeTask(FumoTaskMode.ALWAYS, completed=True)
    assert ScriptTask.active_task_finished(fake) is False
    assert fake.checked == []
    assert fake.goto_main == 0


def test_check_error_treated_as_not_completed():
    fake = FakeTask(FumoTaskMode.ACTIVE_ONLY, raises=GameStuckError('activity page'))
    assert ScriptTask.active_task_finished(fake) is False
    assert fake.goto_main == 1


def test_check_error_restarts_when_main_page_unreachable():
    fake = FakeTask(FumoTaskMode.ACTIVE_ONLY, completed=False)
    fake.ui_goto = lambda page, timeout=60: False
    assert ScriptTask.active_task_finished(fake) is False
    assert fake.restarted == 1


# ---------------------------------------------------------------- 进图提示弹窗
class FakePopupTask:
    """只测 close_enter_popup 的判定，不碰设备"""

    I_FUMO_ENTER_POPUP = 'enter_popup'
    I_FUMO_ENTER_POPUP_CLOSE = 'enter_popup_close'

    def __init__(self, popup_visible=False, close_visible=True):
        self.popup_visible = popup_visible
        self.close_visible = close_visible
        self.clicked = []
        self.cleared = 0
        self.device = SimpleNamespace(click_record_clear=self._clear)

    def _clear(self):
        self.cleared += 1

    def appear(self, target, interval=None, threshold=None):
        if target == self.I_FUMO_ENTER_POPUP:
            return self.popup_visible
        if target == self.I_FUMO_ENTER_POPUP_CLOSE:
            return self.close_visible
        return False

    def appear_then_click(self, target, interval=None):
        if target != self.I_FUMO_ENTER_POPUP_CLOSE or not self.close_visible:
            return False
        self.clicked.append(target)
        return True


def test_enter_popup_closed_when_seen():
    fake = FakePopupTask(popup_visible=True)
    assert ScriptTask.close_enter_popup(fake) is True
    assert fake.clicked == [FakePopupTask.I_FUMO_ENTER_POPUP_CLOSE]
    assert fake.cleared == 1


def test_enter_popup_ignored_when_absent():
    fake = FakePopupTask(popup_visible=False)
    assert ScriptTask.close_enter_popup(fake) is False
    assert fake.clicked == []


def test_enter_popup_reports_failure_when_close_missing():
    fake = FakePopupTask(popup_visible=True, close_visible=False)
    assert ScriptTask.close_enter_popup(fake) is False
    assert fake.clicked == []
    assert fake.cleared == 0


# ---------------------------------------------------------------- 活跃任务进度
class FakeProgressOcr:
    def __init__(self, text):
        self.text = text

    def ocr(self, image):
        return self.text


class FakeProgressTask:
    device = SimpleNamespace(image=object())
    ACTIVE_PROGRESS_TARGET = ScriptTask.ACTIVE_PROGRESS_TARGET

    def __init__(self, text):
        self.O_FUMO_ACTIVE_PROGRESS = FakeProgressOcr(text)


def test_read_active_progress_parses_normal_text():
    assert ScriptTask.read_active_progress(FakeProgressTask('123456/250000')) == (123456, 250000)


def test_read_active_progress_parses_fullwidth_slash_and_spaces():
    assert ScriptTask.read_active_progress(FakeProgressTask('250000 ／ 250000')) == (250000, 250000)


def test_read_active_progress_returns_none_when_unreadable():
    assert ScriptTask.read_active_progress(FakeProgressTask('')) is None


def test_read_active_progress_capped_text_counts_as_full():
    # 满值后游戏不再显示数字，而是「已达上限」
    assert ScriptTask.read_active_progress(
        FakeProgressTask('已达上限')) == (ScriptTask.ACTIVE_PROGRESS_TARGET,
                                          ScriptTask.ACTIVE_PROGRESS_TARGET)


def test_read_active_progress_capped_text_with_ocr_tail():
    # OCR 偶尔会带尾巴：已达上限从其化投育
    assert ScriptTask.read_active_progress(
        FakeProgressTask('已达上限从其化投育')) == (ScriptTask.ACTIVE_PROGRESS_TARGET,
                                                    ScriptTask.ACTIVE_PROGRESS_TARGET)


# ---------------------------------------------------------------- 副本内结束条件
class FakeDungeonTask:
    """只测 handle_dungeon 的结束条件分派，不碰设备"""

    I_FUMO_FINISH_POPUP = 'finish_popup'
    DUNGEON_LOCATION = ScriptTask.DUNGEON_LOCATION
    ACTIVE_PROGRESS_TARGET = ScriptTask.ACTIVE_PROGRESS_TARGET
    MINIMAP_POINTS = ('point_1', 'point_2', 'point_3')
    TARGET_MISS_LIMIT = ScriptTask.TARGET_MISS_LIMIT
    FUMO_LOCK_MAX_SWITCH = ScriptTask.FUMO_LOCK_MAX_SWITCH
    FUMO_LOCK_TIMEOUT = ScriptTask.FUMO_LOCK_TIMEOUT

    def __init__(self, mode, finish_popup=False, progress=None):
        self.config = SimpleNamespace(
            fumo_shanjian=SimpleNamespace(
                fumo_shanjian_config=SimpleNamespace(task_mode=mode)))
        self.finish_popup = finish_popup
        self.progress = progress
        self.closed = 0
        self.exited = 0
        self.points = []
        self.device = SimpleNamespace(sleep=lambda seconds: None, image=object())

    def reset_records(self):
        pass

    def screenshot(self):
        pass

    def appear(self, target, interval=None):
        return self.finish_popup and target == self.I_FUMO_FINISH_POPUP

    def close_finish_popup(self):
        self.closed += 1
        return True

    def read_active_progress(self):
        return self.progress

    def exit_dungeon_by_restart(self):
        self.exited += 1

    def close_enter_popup(self):
        return False

    def in_dungeon(self, retry=3):
        return True

    def map_current_location(self, image=None):
        return None

    def minimap_point(self, index):
        return self.MINIMAP_POINTS[index % len(self.MINIMAP_POINTS)]

    def click_minimap_point(self, rule):
        self.points.append(rule)
        return True

    def ui_target_attackable(self, image=None):
        return False

    def ui_ensure_target_locked(self, **kwargs):
        return False

    def battle_attack(self):
        return 'target_lost'

    def battle_interrupted(self):
        return False


def test_handle_dungeon_finish_popup_closes_and_returns_finished():
    # 进副本前/复活后如果已经有结算弹窗，先关掉并结束本轮
    fake = FakeDungeonTask(FumoTaskMode.ALWAYS, finish_popup=True)
    assert ScriptTask.handle_dungeon(fake) == 'finished'
    assert fake.closed == 1
    assert fake.exited == 0

    fake = FakeDungeonTask(FumoTaskMode.ACTIVE_ONLY, finish_popup=True)
    assert ScriptTask.handle_dungeon(fake) == 'finished'
    assert fake.closed == 1
    assert fake.exited == 0


class FakeOutsideTask(FakeDungeonTask):
    def __init__(self, mode=FumoTaskMode.ACTIVE_ONLY):
        super().__init__(mode)
        self.reentered = 0

    def in_dungeon(self, retry=3):
        return False

    def enter_dungeon_flow(self):
        self.reentered += 1
        self.finish_popup = True
        return True


def test_handle_dungeon_reenters_when_outside_dungeon():
    # 掉线重启/死亡复活后被送出副本：当前步骤内重新进副本继续
    fake = FakeOutsideTask()
    assert ScriptTask.handle_dungeon(fake) == 'finished'
    assert fake.reentered == 1
    assert fake.closed == 1
    assert fake.points == ['point_1']


def test_handle_dungeon_active_mode_exits_when_progress_full():
    fake = FakeDungeonTask(FumoTaskMode.ACTIVE_ONLY, progress=(250000, 250000))
    assert ScriptTask.handle_dungeon(fake) == 'active_done'
    assert fake.exited == 1
    assert fake.closed == 0


class FakeFightTask(FakeDungeonTask):
    """模拟到达后连续锁不到目标：验证 2 次失败后换点、三点循环"""

    def __init__(self):
        super().__init__(FumoTaskMode.ALWAYS)
        self.lock_attempts = 0

    def map_current_location(self, image=None):
        return ('伏魔涧', 100, 100)  # 坐标一直不变 = 已到达

    def ui_ensure_target_locked(self, **kwargs):
        self.lock_attempts += 1
        if self.lock_attempts >= 9:
            self.finish_popup = True
        return False

    def battle_interrupted(self):
        return self.finish_popup


def test_handle_dungeon_cycles_minimap_points_after_two_misses():
    fake = FakeFightTask()
    assert ScriptTask.handle_dungeon(fake) == 'finished'
    assert fake.points == ['point_1', 'point_2', 'point_3']
    assert fake.lock_attempts == 9


class FakeMovingTask(FakeDungeonTask):
    """模拟还在移动（坐标每次都变）：锁不到目标也不能算失败"""

    def __init__(self):
        super().__init__(FumoTaskMode.ALWAYS)
        self.lock_attempts = 0
        self.position = 100

    def map_current_location(self, image=None):
        self.position += 1
        return ('伏魔涧', self.position, self.position)

    def ui_ensure_target_locked(self, **kwargs):
        self.lock_attempts += 1
        if self.lock_attempts >= 5:
            self.finish_popup = True
        return False

    def battle_interrupted(self):
        return self.finish_popup


def test_handle_dungeon_moving_misses_do_not_switch_point():
    fake = FakeMovingTask()
    assert ScriptTask.handle_dungeon(fake) == 'finished'
    assert fake.points == ['point_1']  # 一直在移动，不换点
    assert fake.lock_attempts == 5


class FakeAttackTask(FakeDungeonTask):
    """模拟已经锁到可攻击目标：直接进入战斗"""

    def __init__(self):
        super().__init__(FumoTaskMode.ALWAYS)
        self.attacked = 0

    def ui_target_attackable(self, image=None):
        return True

    def battle_attack(self):
        self.attacked += 1
        self.finish_popup = True
        return 'finished'


def test_handle_dungeon_attacks_when_target_attackable():
    fake = FakeAttackTask()
    assert ScriptTask.handle_dungeon(fake) == 'finished'
    assert fake.attacked == 1
    assert fake.closed == 1


class FakePointTask:
    MINIMAP_POINTS = ('point_1', 'point_2', 'point_3')
    point_1 = A1 = 'p1'
    point_2 = A2 = 'p2'
    point_3 = A3 = 'p3'


def test_minimap_point_cycles_in_order():
    fake = FakePointTask()
    assert ScriptTask.minimap_point(fake, 0) == 'p1'
    assert ScriptTask.minimap_point(fake, 1) == 'p2'
    assert ScriptTask.minimap_point(fake, 2) == 'p3'
    assert ScriptTask.minimap_point(fake, 3) == 'p1'


# ---------------------------------------------------------------- 提前退出后重进副本
class FakeRunTask:
    """只测 run() 的「活跃模式提前退出后重进副本」循环，不碰设备"""

    ACTIVE_TASK_NAME = '伏魔山涧'

    def __init__(self, mode, results):
        self.config = SimpleNamespace(
            config_name='oas1',
            fumo_shanjian=SimpleNamespace(
                fumo_shanjian_config=SimpleNamespace(task_mode=mode)))
        self.results = list(results)
        self.prepared = 0
        self.opened = 0
        self.entered = 0
        self.scheduled = []

    def run_step(self, name, func):
        return func()

    def prepare_game(self):
        self.prepared += 1

    def active_task_finished(self):
        return False

    def open_npc_dialog(self):
        self.opened += 1
        return True

    def enter_dungeon(self):
        self.entered += 1
        return True

    def enter_dungeon_flow(self):
        if not self.open_npc_dialog():
            return False
        return self.enter_dungeon()

    def handle_dungeon(self):
        return self.results.pop(0)

    def set_next_run(self, **kwargs):
        self.scheduled.append(kwargs)


def test_active_mode_reenters_dungeon_until_progress_done():
    fake = FakeRunTask(FumoTaskMode.ACTIVE_ONLY, ['finished', 'finished', 'active_done'])
    with pytest.raises(TaskEnd):
        ScriptTask.run(fake)
    assert fake.opened == 3
    assert fake.entered == 3
    assert fake.prepared == 3  # 首次 + 两次重进前回主页面
    assert fake.scheduled


def test_always_mode_ends_after_first_finish():
    fake = FakeRunTask(FumoTaskMode.ALWAYS, ['finished'])
    with pytest.raises(TaskEnd):
        ScriptTask.run(fake)
    assert fake.opened == 1
    assert fake.entered == 1


# ---------------------------------------------------------------- 死亡复活续跑
class FakeDeathTask:
    I_DEATH_DIALOG = 'death_dialog'

    def __init__(self, dead=True):
        self.dead = dead
        self.revived = 0
        self.reran = 0
        self.resets = 0
        self._current_step = '进入副本'
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def appear(self, target, interval=None):
        return self.dead and target == self.I_DEATH_DIALOG

    def revive(self):
        self.revived += 1

    def rerun_task(self):
        self.reran += 1

    def ui_reset_current_page(self):
        self.resets += 1

    def handle_death_resume(self):
        self.ui_reset_current_page()
        raise DeathResume


def test_handle_death_revives_and_raises_resume():
    fake = FakeDeathTask(dead=True)
    with pytest.raises(DeathResume):
        ScriptTask.handle_death(fake)
    assert fake.revived == 1
    assert fake.reran == 0  # 不重跑整个任务
    assert fake.resets == 1


def test_handle_death_ignores_no_death():
    fake = FakeDeathTask(dead=False)
    assert ScriptTask.handle_death(fake) is None
    assert fake.revived == 0
    assert fake.reran == 0


class FakeStepTask:
    def __init__(self):
        self.calls = 0
        self.resets = 0
        self.device = SimpleNamespace(sleep=lambda seconds: None)

    def ui_reset_current_page(self):
        self.resets += 1


def test_run_step_retries_same_step_after_death():
    fake = FakeStepTask()

    def step():
        fake.calls += 1
        if fake.calls == 1:
            raise DeathResume
        return 'ok'

    assert ScriptTask.run_step(fake, '测试步骤', step) == 'ok'
    assert fake.calls == 2
    assert fake.resets == 1
    assert fake._current_step == '测试步骤'
