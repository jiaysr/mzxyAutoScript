# -*- coding: utf-8 -*-
"""
世界任务「打怪类」任务单测（兽毛之争 / 黄金比例，不连设备）

真机玩法（2026-11-05 确认）：
1. 打开小地图、视口拖到指定角落（兽毛之争=右下角，黄金比例=左上角），点刷新点；
2. 关掉小地图，人物自动走到打架位（兽毛之争 226,375，黄金比例 220,156）；
3. 锁目标（野兽 / 黄金鼠）并按任务「战斗配置」里的预设放技能；怪 4~5 秒刷新，
   所以目标丢了直接重新锁，不用补锁逻辑；
4. 打的同时**每 5 秒查一次坐标**，偏离打架位就回点位再接着打，直到任务完成。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_fight.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_fight.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.base.timer import Timer
from tasks.WorldTask.script_task import ScriptTask

BEAST = ('兽毛之争', '野兽', (226, 375), 'bottom_right')
GOLDEN = ('黄金比例', '黄金鼠', (220, 156), 'top_left')


class FakeDevice:
    image = None

    def __init__(self):
        self.clicks = []

    def click(self, x=None, y=None, control_name=None, **kwargs):
        self.clicks.append((x, y, control_name))

    def click_record_clear(self):
        pass

    def sleep(self, seconds):
        pass

    def stuck_record_clear(self):
        pass


def make_task():
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(
            location='域外灵岛', bonus_multiplier=None)))
    return task


# ---------------------------------------------------------------- 任务表
def test_fight_task_table():
    """两个打怪任务：目标名 / 打架位 / 小地图锚点角 / 刷新点坐标都要对"""
    table = {row[0]: row for row in ScriptTask.FIGHT_TASKS}
    assert table['兽毛之争'][1:] == ('野兽', (226, 375), 'bottom_right')
    assert table['黄金比例'][1:] == ('黄金鼠', (220, 156), 'top_left')

    task = make_task()
    assert task.fight_click_rule('兽毛之争').coord() == (455, 407)
    assert task.fight_click_rule('黄金比例').coord() == (536, 371)


# ---------------------------------------------------------------- 坐标跑偏
def test_fight_drifted():
    task = make_task()
    readings = [('世界之巅', 226, 375), ('世界之巅', 240, 375), None]
    task.map_move_read_pos = lambda retry=None: readings.pop(0)

    assert task.fight_drifted((226, 375)) is False, '正好在打架位，没跑偏'
    assert task.fight_drifted((226, 375)) is True, '偏了 14 格，要回点位'
    assert task.fight_drifted((226, 375)) is False, '坐标读不出来按没跑偏处理'


# ---------------------------------------------------------------- 停止条件
def test_fight_stop_reason_complete():
    task = make_task()
    task.task_completed = lambda name: True
    assert task.fight_stop_reason('兽毛之争', (226, 375)) == 'complete'


def test_fight_stop_reason_drift():
    """每 5 秒才查一次坐标：计时没到不查，到了且跑偏就返回 drift"""
    task = make_task()
    task.task_completed = lambda name: False
    calls = []
    task.fight_drifted = lambda position: calls.append(1) or True

    task.fight_drift_timer = Timer(60).start()      # 还没到 5 秒
    assert task.fight_stop_reason('兽毛之争', (226, 375)) is None
    assert calls == [], '计时没到不该去查坐标（查坐标要 OCR）'

    task.fight_drift_timer = Timer(-1).start()      # 立刻到点（0 秒在 Windows 上可能同毫秒不算到）
    assert task.fight_stop_reason('兽毛之争', (226, 375)) == 'drift'
    assert calls == [1]


# ---------------------------------------------------------------- 去点位
def make_goto_task(task_name, corner, click_xy):
    task = make_task()
    events = []
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 300, 500)   # 人还在远处
    task.map_open_minimap_tap = lambda timeout=8: events.append('open') or True
    task.map_pan_to_corner = lambda c, **kwargs: events.append(f'pan:{c}') or True
    task.close_minimap_if_open = lambda timeout=3: events.append('close') or True
    task.map_wait_arrive_at = lambda position, **kwargs: events.append(f'arrive:{position}') or True
    task.expected_click = click_xy
    task.expected_corner = corner
    return task, events


def test_fight_goto_point_beast():
    task, events = make_goto_task('兽毛之争', 'bottom_right', (455, 407))
    assert task.fight_goto_point('兽毛之争', (226, 375), 'bottom_right') is True
    assert events == ['open', 'pan:bottom_right', 'close', 'arrive:(226, 375)'], events
    assert task.device.clicks[0][:2] == (455, 407), '小地图点位要精确点到坐标上'
    assert task.device.clicks[0][2] == 'beast_point'


def test_fight_goto_point_golden():
    """黄金比例：左上角 + 另一个刷新点，不能跟兽毛之争混用"""
    task, events = make_goto_task('黄金比例', 'top_left', (536, 371))
    assert task.fight_goto_point('黄金比例', (220, 156), 'top_left') is True
    assert events == ['open', 'pan:top_left', 'close', 'arrive:(220, 156)'], events
    assert task.device.clicks[0][:2] == (536, 371), '小地图点位要精确点到坐标上'
    assert task.device.clicks[0][2] == 'golden_point'


def test_fight_goto_point_skips_when_already_near():
    """被打退几步、人还在打架位附近：不重开地图（重开一次要 5s+）"""
    task = make_task()
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 230, 378)   # 容差内
    opened = []
    task.map_open_minimap_tap = lambda timeout=8: opened.append(1) or True
    task.map_wait_arrive_at = lambda position, **kwargs: True

    assert task.fight_goto_point('兽毛之争', (226, 375), 'bottom_right') is True
    assert opened == [], '在点位附近就不该再开地图'
    assert task.device.clicks == []


def test_fight_goto_point_pan_fails():
    task = make_task()
    task.map_move_read_pos = lambda retry=None: ('世界之巅', 300, 500)
    task.map_open_minimap_tap = lambda timeout=8: True
    task.map_pan_to_corner = lambda corner, **kwargs: False
    task.map_wait_arrive_at = lambda position, **kwargs: True

    assert task.fight_goto_point('兽毛之争', (226, 375), 'bottom_right') is False
    assert task.device.clicks == [], '地图没拖到位就不该点刷新点'


# ---------------------------------------------------------------- 打怪循环
def test_run_fight_task_skips_when_completed():
    task = make_task()
    task.task_completed = lambda name: True
    called = []
    task.fight_goto_point = lambda *args: called.append('goto') or True

    assert task.run_fight_task(*BEAST) is True
    assert called == []


def test_fight_returns_when_completed():
    task = make_task()
    flags = [False, True]        # 进循环时没完成，打完一轮报完成
    task.task_completed = lambda name: flags.pop(0) if len(flags) > 1 else flags[0]
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0, 1]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: True
    locked = []
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: \
        locked.append(target_name) or True
    reasons = ['target_lost']
    task.battle_skill_loop = lambda slots, **kwargs: reasons.pop(0) if reasons else 'timeout'

    assert task.fight_until_complete(*BEAST) is True
    assert locked and locked[0] == '野兽', locked


def test_fight_regotos_on_drift():
    """打的过程中跑偏：回一次点位再接着打（用该任务自己的锚点角和刷新点）"""
    task = make_task()
    task.task_completed = lambda name: False
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: True
    regotos = []
    task.fight_goto_point = lambda *args, **kwargs: regotos.append(args) or True

    reasons = ['drift', 'target_lost', 'complete']
    def loop(slots, **kwargs):
        reason = reasons.pop(0)
        if reason == 'complete':
            task.task_completed = lambda name: True
            return 'target_lost'
        return reason
    task.battle_skill_loop = loop

    assert task.fight_until_complete(*GOLDEN) is True
    assert regotos == [('黄金比例', (220, 156), 'top_left')], regotos


def test_fight_gives_up_when_target_never_locks():
    task = make_task()
    task.task_completed = lambda name: False
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: False
    task.FIGHT_TIMEOUT = 0.3

    assert task.fight_until_complete(*BEAST) is False


# ---------------------------------------------------------------- 任务页状态
def test_read_task_page_statuses_unreadable_means_pending():
    """任务页打不开：按没做处理，不误跳过"""
    task = make_task()
    task.open_task_page = lambda timeout=30: False
    assert task.read_task_page_statuses(['兽毛之争']) == [('兽毛之争', 'pending')]


def test_read_task_page_statuses_marks_done():
    task = make_task()
    task.open_task_page = lambda timeout=30: True
    task.close_task_page = lambda timeout=10: True
    task.read_all_task_rows = lambda max_scroll=4: [
        ('【跨服】兽毛之争', '完成', 1093, 324),
        ('【跨服】黄金比例', '进行中', 1093, 402),
    ]
    result = dict(task.read_task_page_statuses(['兽毛之争', '黄金比例', '世界之心']))
    assert result == {'兽毛之争': 'done', '黄金比例': 'pending', '世界之心': 'missing'}, result


# ---------------------------------------------------------------- 为了荣耀（分阵营）
def test_glory_variants_table():
    """两个阵营的目标 / 打架位 / 锚点角 / 点位都不一样"""
    task = make_task()
    variants = ScriptTask.GLORY_VARIANTS
    assert variants['huangdi'][:3] == ('炎帝守门人', (400, 316), 'bottom_right')
    assert variants['yandi'][:3] == ('黄帝守门人', (90, 128), 'top_left')
    assert task.fight_click_rule(variants['huangdi'][3]).coord() == (749, 305)
    assert task.fight_click_rule(variants['yandi'][3]).coord() == (315, 325)


def test_detect_camp():
    task = make_task()
    readings = [('世界之巅', 60, 65), ('世界之巅', 439, 384), ('世界之巅', 300, 300)]
    task.map_move_read_pos = lambda retry=None: readings.pop(0)

    assert task.detect_camp() == 'huangdi'
    assert task.detect_camp() == 'yandi'
    assert task.detect_camp() is None, '不在任何出生点附近就判断不出来'


def test_run_glory_known_camp():
    """阵营已知：只打对应那一个变体"""
    task = make_task()
    task.camp = 'yandi'
    task.task_completed = lambda name: False
    calls = []
    task.run_glory_variant = lambda camp, lock_timeout=None: calls.append((camp, lock_timeout)) or True

    assert task.run_glory_task() is True
    assert calls == [('yandi', None)], calls


def test_run_glory_unknown_camp_tries_both():
    """阵营未知：两个点位都试，第一个锁不到目标就换第二个"""
    task = make_task()
    task.camp = None
    task.task_completed = lambda name: False
    task.detect_camp = lambda: None
    calls = []
    def variant(camp, lock_timeout=None):
        calls.append(camp)
        return camp == 'yandi'          # 黄帝点位锁不到目标，炎帝可以
    task.run_glory_variant = variant

    assert task.run_glory_task() is True
    assert calls == ['huangdi', 'yandi'], calls
    assert task.camp == 'yandi', '试出来之后要记住阵营'


def test_run_glory_both_variants_fail():
    task = make_task()
    task.camp = None
    task.task_completed = lambda name: False
    task.detect_camp = lambda: None
    task.run_glory_variant = lambda camp, lock_timeout=None: False

    assert task.run_glory_task() is False
    assert task.camp is None


def test_run_glory_skips_when_completed():
    task = make_task()
    task.task_completed = lambda name: True
    called = []
    task.run_glory_variant = lambda camp, lock_timeout=None: called.append(camp) or True

    assert task.run_glory_task() is True
    assert called == []


def test_fight_lock_timeout_gives_up():
    """探点位时：一直锁不到目标就按超时放弃（不然会干等 10 分钟）"""
    task = make_task()
    task.task_completed = lambda name: False
    task.battle_preset = lambda: {}
    task.battle_plan_slots = lambda preset=None: [0]
    task.battle_use_normal_attack = lambda: False
    task.ui_ensure_target_locked = lambda target_name=None, action=None, max_switch=None: False

    assert task.fight_until_complete('为了荣耀', '炎帝守门人', (400, 316), 'bottom_right',
                                     rule_key='glory_huangdi', lock_timeout=0.2) is False


def main():
    cases = [
        test_fight_task_table,
        test_fight_drifted,
        test_fight_stop_reason_complete,
        test_fight_stop_reason_drift,
        test_fight_goto_point_beast,
        test_fight_goto_point_golden,
        test_fight_goto_point_skips_when_already_near,
        test_fight_goto_point_pan_fails,
        test_run_fight_task_skips_when_completed,
        test_fight_returns_when_completed,
        test_fight_regotos_on_drift,
        test_fight_gives_up_when_target_never_locks,
        test_read_task_page_statuses_unreadable_means_pending,
        test_read_task_page_statuses_marks_done,
        test_glory_variants_table,
        test_detect_camp,
        test_run_glory_known_camp,
        test_run_glory_unknown_camp_tries_both,
        test_run_glory_both_variants_fail,
        test_run_glory_skips_when_completed,
        test_fight_lock_timeout_gives_up,
    ]
    failed = 0
    for case in cases:
        try:
            case()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {case.__name__}: {e}')
        else:
            print(f'PASS {case.__name__}')
    if failed:
        print(f'\n{failed} / {len(cases)} failed')
        sys.exit(1)
    print(f'\nall {len(cases)} passed')


if __name__ == '__main__':
    main()
