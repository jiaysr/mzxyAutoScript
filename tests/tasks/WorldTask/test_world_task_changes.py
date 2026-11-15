# -*- coding: utf-8 -*-
"""世界任务回归测试（不连设备）：阵营探测 / 任务榜关闭 / 世界之心刷怪点配置"""
from types import SimpleNamespace

from tasks.GameUi.core.ocr import OcrHelpers
from tasks.WorldTask.config import DropFarmTask, WorldTask
from tasks.WorldTask.script_task import ScriptTask


# ---------------------------------------------------------------- 配置
def test_config_drops_location_and_has_drop_farm_task():
    # 旧配置里的 location 必须被忽略（字段已删，pydantic extra=ignore）
    config = WorldTask(**{'world_task_config': {'location': '别的地图'}}).world_task_config
    assert not hasattr(config, 'location')
    assert config.drop_farm_task == DropFarmTask.GLORY

    config = WorldTask(**{'world_task_config': {'drop_farm_task': '兽毛之争'}}).world_task_config
    assert config.drop_farm_task == DropFarmTask.BEAST


# ---------------------------------------------------------------- 探阵营
class FakeProbe:
    GLORY_VARIANTS = {
        'huangdi': ('炎帝守门人', (327, 363), 'bottom_right', 'glory_huangdi'),
        'yandi': ('黄帝守门人', (90, 128), 'top_left', 'glory_yandi'),
    }

    def __init__(self, attackable):
        self.attackable = set(attackable)
        self.probed = []
        self.camp = None

    def probe_glory_variant(self, camp):
        self.probed.append(camp)
        return camp in self.attackable


def test_probe_camp_picks_the_attackable_side():
    # 黄帝阵营的对家（炎帝守门人）可打 -> camp=huangdi
    fake = FakeProbe({'huangdi'})
    assert ScriptTask.probe_camp(fake) == 'huangdi'
    assert fake.camp == 'huangdi'
    assert fake.probed == ['huangdi']          # 第一个就对，不用再看第二个

    # 黄帝点位打不了、炎帝点位可打 -> camp=yandi，两个点位都试过
    fake = FakeProbe({'yandi'})
    assert ScriptTask.probe_camp(fake) == 'yandi'
    assert fake.camp == 'yandi'
    assert fake.probed == ['huangdi', 'yandi']

    # 两边都不可打 -> None，不记 camp
    fake = FakeProbe(set())
    assert ScriptTask.probe_camp(fake) is None
    assert fake.camp is None


# ---------------------------------------------------------------- 任务榜关闭
class FakeBoard:
    I_CLOSE_X_GREEN = object()

    def __init__(self, states=None, always_open=False):
        self.states = list(states or [])
        self.always_open = always_open
        self.clicks = 0
        self.screens = 0
        self.device = SimpleNamespace(click_record_clear=lambda: None,
                                      sleep=lambda seconds: None)

    def screenshot(self):
        self.screens += 1
        return object()

    def task_board_open(self, image=None):
        if self.always_open:
            return True
        return self.states.pop(0) if self.states else False

    def appear_then_click(self, target, interval=None):
        self.clicks += 1
        return True


def test_close_task_board_clicks_x_until_clean():
    # 开 -> 点X -> 开 -> 点X -> 连续 3 次干净才收工
    fake = FakeBoard([True, True, False, False, False])
    assert ScriptTask.close_task_board(fake, timeout=30) is True
    assert fake.clicks == 2
    assert fake.screens == 5


def test_close_task_board_gives_up_when_never_closed():
    fake = FakeBoard(always_open=True)
    assert ScriptTask.close_task_board(fake, timeout=0.3) is False
    assert fake.clicks >= 1


# ---------------------------------------------------------------- 世界之心刷怪点
class FakeFarm(ScriptTask):
    """只测 farm_for_drop 的分派，不碰设备（FIGHT_TASKS 直接用真实表）"""

    def __init__(self, task):  # 不调父类 __init__（会连设备/OCR）
        self.task = task
        self.calls = []

    def drop_farm_task(self):
        return self.task

    def farm_for_drop_glory(self, seconds):
        self.calls.append(('glory', seconds))

    def fight_goto_point(self, task_name, position, corner, rule_key=None):
        self.calls.append(('goto', task_name, position, corner, rule_key))
        return True

    def fight_until_complete(self, task_name, target, position, corner,
                             rule_key=None, timeout=None):
        self.calls.append(('fight', task_name, target, position, corner, rule_key, timeout))
        return True


def test_farm_for_drop_glory_uses_camp_logic():
    fake = FakeFarm('为了荣耀')
    fake.farm_for_drop(seconds=30)
    assert fake.calls == [('glory', 30)]


def test_farm_for_drop_fight_task_uses_its_own_point():
    fake = FakeFarm('黄金比例')
    fake.farm_for_drop(seconds=30)
    assert fake.calls == [
        ('goto', '黄金比例', (220, 156), 'top_left', None),
        ('fight', '世界之心', ('黄金鼠', '蚩尤使者'), (220, 156), 'top_left', '黄金比例', 30),
    ]


def test_farm_for_drop_unknown_task_does_nothing():
    fake = FakeFarm('不存在')
    fake.farm_for_drop(seconds=30)
    assert fake.calls == []


# ---------------------------------------------------------------- 打怪目标（多目标）
class FakeTargetTask:
    """只测 fight_target_ok：锁定状态一律「可攻击」，名字由构造传入"""

    TARGET_NAME_CHECK_INTERVAL = 0
    ocr_name_match = OcrHelpers.ocr_name_match
    fight_target_names = staticmethod(ScriptTask.fight_target_names)
    fight_target_text = ScriptTask.fight_target_text
    fight_target_name_ok = ScriptTask.fight_target_name_ok

    def __init__(self, name):
        self.name = name
        self._target_name_cache = None

    def ui_target_attackable(self, image=None):
        return True

    def ui_target_name(self, scales=None, min_score=None):
        return self.name


def test_fight_tasks_include_sunday_extra_target():
    """周日才刷的蚩尤使者挂在两个打怪任务的额外目标上"""
    rows = {row[0]: row for row in ScriptTask.FIGHT_TASKS}
    for name in ('兽毛之争', '黄金比例'):
        targets = ScriptTask.fight_target_names(rows[name][1])
        assert '蚩尤使者' in targets
        assert len(targets) == 2


def test_fight_target_names_accepts_str_list_and_comma():
    assert ScriptTask.fight_target_names('野兽') == ('野兽',)
    assert ScriptTask.fight_target_names('野兽, 蚩尤使者') == ('野兽', '蚩尤使者')
    assert ScriptTask.fight_target_names(['野兽', '蚩尤使者']) == ('野兽', '蚩尤使者')
    assert ScriptTask.fight_target_names(None) == ()
    assert ScriptTask.fight_target_text(('野兽', '蚩尤使者')) == '野兽/蚩尤使者'


def test_fight_target_ok_accepts_any_target_of_the_list():
    targets = ('野兽', '蚩尤使者')
    assert ScriptTask.fight_target_ok(FakeTargetTask('野兽'), targets) is True
    assert ScriptTask.fight_target_ok(FakeTargetTask('蚩尤使者'), targets) is True
    # 逗号分隔的老写法也要认
    assert ScriptTask.fight_target_ok(FakeTargetTask('蚩尤使者'), '野兽,蚩尤使者') is True


def test_fight_target_ok_rejects_other_targets():
    targets = ('野兽', '蚩尤使者')
    # 敌对玩家 / 无关 NPC 都不算
    assert ScriptTask.fight_target_ok(FakeTargetTask('素问(8区)'), targets) is False
    assert ScriptTask.fight_target_ok(FakeTargetTask('黄金鼠'), targets) is False
    # 名字读不出来时按原目标处理（别因为一次 OCR 抖动就停手）
    assert ScriptTask.fight_target_ok(FakeTargetTask(''), targets) is True


# ---------------------------------------------------------------- 界面被遮挡时脱困
class FakeBlindTask:
    BLIND_ROUNDS = 2
    BLIND_ESCAPE_LIMIT = 3
    BLIND_OK = 'ok'
    BLIND_ESCAPED = 'escaped'
    BLIND_GIVE_UP = 'give_up'

    def __init__(self, hud_readable=False, close_works=False, reenter_works=False):
        self.hud = hud_readable
        self.close_works = close_works
        self.reenter_works = reenter_works
        self.close_calls = 0
        self.reset_calls = 0
        self.reenter_calls = 0
        self._blind_rounds = 0
        self._blind_escapes = 0

    def hud_readable(self):
        return self.hud

    def try_close_unknown_page(self, skip_screenshot=True):
        self.close_calls += 1
        if self.close_works:
            self.hud = True        # 弹窗关掉后 HUD 回得来
        return self.close_works

    def ui_reset_current_page(self):
        self.reset_calls += 1

    def ensure_world_peak_map(self, timeout=40):
        self.reenter_calls += 1
        if self.reenter_works:
            self.hud = True        # 重新进图后 HUD 回得来
        return self.reenter_works


def test_blind_check_does_nothing_when_hud_visible():
    fake = FakeBlindTask(hud_readable=True)
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_OK
    assert fake.close_calls == 0
    assert fake.reenter_calls == 0


def test_blind_check_needs_consecutive_rounds_then_closes_popup():
    fake = FakeBlindTask(hud_readable=False, close_works=True)
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_OK   # 第 1 轮：先只记一笔
    assert fake.close_calls == 0
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_ESCAPED  # 第 2 轮：关弹窗
    assert fake.close_calls == 1
    assert fake.reset_calls == 0                                        # 关掉了就不用重新进图


def test_blind_check_reenters_map_when_popup_cannot_be_closed():
    fake = FakeBlindTask(hud_readable=False, close_works=False, reenter_works=True)
    ScriptTask.fight_blind_check(fake)
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_ESCAPED
    assert fake.close_calls == 1
    assert fake.reset_calls == 1
    assert fake.reenter_calls == 1


def test_blind_check_gives_up_after_escape_limit():
    fake = FakeBlindTask(hud_readable=False, close_works=False, reenter_works=False)
    results = []
    for _ in range(ScriptTask.BLIND_ESCAPE_LIMIT * 2):
        results.append(ScriptTask.fight_blind_check(fake))
    # 每两次「看不见」才尝试一次脱困，第 BLIND_ESCAPE_LIMIT 次后放弃
    assert results.count(ScriptTask.BLIND_GIVE_UP) == 1
    assert results[-1] == ScriptTask.BLIND_GIVE_UP
    assert fake.reenter_calls == ScriptTask.BLIND_ESCAPE_LIMIT


def test_blind_check_counter_resets_when_hud_comes_back():
    fake = FakeBlindTask(hud_readable=False, close_works=True)
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_OK
    fake.hud = True
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_OK
    assert fake._blind_rounds == 0
    assert fake._blind_escapes == 0
    fake.hud = False
    assert ScriptTask.fight_blind_check(fake) == ScriptTask.BLIND_OK   # 重新从第 1 轮数
