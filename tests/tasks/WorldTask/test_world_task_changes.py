# -*- coding: utf-8 -*-
"""世界任务回归测试（不连设备）：阵营探测 / 任务榜关闭 / 世界之心刷怪点配置"""
from types import SimpleNamespace

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
    """只测 farm_for_drop 的分派，不碰设备"""

    FIGHT_TASKS = (
        ('兽毛之争', '野兽', (226, 375), 'bottom_right'),
        ('黄金比例', '黄金鼠', (220, 156), 'top_left'),
    )

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
        ('fight', '世界之心', '黄金鼠', (220, 156), 'top_left', '黄金比例', 30),
    ]


def test_farm_for_drop_unknown_task_does_nothing():
    fake = FakeFarm('不存在')
    fake.farm_for_drop(seconds=30)
    assert fake.calls == []
