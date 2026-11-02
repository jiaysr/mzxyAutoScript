# -*- coding: utf-8 -*-
"""
域外迷窟「接进战斗预设体系 + 战斗目标白名单」单测（不连设备）

覆盖：
- 战斗配置的默认值（预设 / 普通攻击关闭 / 秒杀快打 / 目标白名单）
- battle_task_preset 从嵌套的 battle_config 组里取预设 id（不会静默回落系统预设）
- 目标白名单的解析（中英文逗号、分号、空格）
- acquire_target 把白名单和动作要求透传给 ui_ensure_target_locked，连续失败有兜底
- 按预设模式走 battle_plan_slots + battle_skill_loop（而不是旧的硬编码连点）
- 秒杀快打模式走固定序列，不再 blindly 点「目标」
- 停止条件只有时段到点（怪物在时段内一直刷新，没有「打完」）
- 离场依次点三个出口（每个「开图 -> 拖到右上角 -> 点 -> 等 60s」）
- handle_window 按 fast_click 分发，且无论成败都会离开迷窟

    python tests\\test_yuwai_battle_preset.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.YuwaiMimang.config import YuwaiBattleConfig, YuwaiMimang
from tasks.YuwaiMimang.script_task import ScriptTask

PRESET = {'id': 'preset_7', 'name': '测试预设',
          'order': ['badao_1', 'badao_2'], 'disabled': [], 'builtin': False}


def make_task(battle_config: YuwaiBattleConfig = None):
    """
    只造对象、不跑 __init__：这些方法都是纯逻辑，不需要真设备/真配置。

    config 的层次和真配置一致：yuwai_mimang.battle_config 是嵌套组，
    model.yuwai_mimang 供 BattleAttack.battle_use_normal_attack() 走 command 反查。
    """
    task = ScriptTask.__new__(ScriptTask)
    battle = battle_config or YuwaiBattleConfig()
    task.config = SimpleNamespace(
        yuwai_mimang=SimpleNamespace(battle_config=battle),
        model=SimpleNamespace(yuwai_mimang=SimpleNamespace(battle_config=battle)),
        task=SimpleNamespace(command='YuwaiMimang'),
        battle=None)
    task.device = SimpleNamespace(sleep=lambda seconds: None, image=None)
    task.reset_records = lambda: None
    task.screenshot = lambda: None
    task.recover_in_battle = lambda coord, tolerance: True
    task.need_repair = lambda cfg, last_repair, end: False
    task.repair_in_battle = lambda coord, tolerance: None
    task.battle_preset = lambda: PRESET
    return task, battle


# ------------------------------------------------------------------ 配置默认值
def test_config_defaults():
    battle = YuwaiBattleConfig()
    assert battle.battle_preset == 'default', battle.battle_preset
    assert battle.use_normal_attack is False, '本任务不点普通攻击，沿用共享默认（关）'
    assert battle.fast_click is False, '默认按预设打'
    assert battle.fast_relock is True, '迷窟是连续刷怪 + 限时多杀，补锁默认开'
    assert battle.target_names == '小鬼,鬼王,鬼将', battle.target_names

    task_config = YuwaiMimang()
    assert isinstance(task_config.battle_config, YuwaiBattleConfig)
    print('test_config_defaults OK')


def test_battle_task_preset_reads_nested_group():
    task, battle = make_task()
    battle.battle_preset = 'preset_1'
    assert task.battle_task_preset() == 'preset_1', \
        '必须读 battle_config 组里的字段，否则会静默回落系统预设'
    battle.battle_preset = ''
    assert task.battle_task_preset() == ''
    print('test_battle_task_preset_reads_nested_group OK')


# ------------------------------------------------------------------ 目标白名单
def test_battle_target_names_parsing():
    task, battle = make_task()

    battle.target_names = '小鬼,鬼王,鬼将'
    assert task.battle_target_names() == ['小鬼', '鬼王', '鬼将']

    # 中文逗号 / 分号 / 空格 / 多余空白都要能切开
    battle.target_names = ' 小鬼，鬼王; 鬼将  花妖 '
    assert task.battle_target_names() == ['小鬼', '鬼王', '鬼将', '花妖']

    battle.target_names = '   '
    assert task.battle_target_names() == [], '空配置 = 不校验名字'
    print('test_battle_target_names_parsing OK')


def test_acquire_target_passes_whitelist():
    task, _ = make_task()
    captured = {}

    def fake_lock(**kwargs):
        captured.update(kwargs)
        return True

    task.ui_ensure_target_locked = fake_lock
    assert task.acquire_target(['小鬼', '鬼王']) is True
    assert captured['target_name'] == ['小鬼', '鬼王'], captured
    assert captured['action'] == 'attack', captured
    assert captured['max_switch'] == task.TARGET_SWITCH_MAX
    assert captured['timeout'] == task.TARGET_SWITCH_TIMEOUT

    # 白名单为空时传 None（= 只要求可攻击，不校验名字）
    captured.clear()
    task.acquire_target([])
    assert captured['target_name'] is None, captured
    print('test_acquire_target_passes_whitelist OK')


# ------------------------------------------------------------------ 按预设模式
def test_acquire_target_never_falls_back_to_other_targets():
    """锁不到白名单目标就继续找，绝不退回「打任意目标」（迷窟里会打到玩家）"""
    task, _ = make_task()
    calls = []

    def fake_lock(**kwargs):
        calls.append(kwargs)
        return False

    task.ui_ensure_target_locked = fake_lock
    task._target_fail_count = 0

    for _ in range(task.TARGET_WARN_AFTER + 2):
        assert task.acquire_target(['小鬼']) is False
    assert all(c['target_name'] == ['小鬼'] for c in calls), '不允许退化成不校验名字'
    print('test_acquire_target_never_falls_back_to_other_targets OK')


def test_preset_loop_uses_skill_loop():
    task, battle = make_task()
    calls = []
    task.acquire_target = lambda names=None: True
    task.battle_plan_slots = lambda: [2, 0]
    task.ui_target_attackable = lambda image=None: True

    def fake_loop(slots, **kwargs):
        calls.append((list(slots), kwargs))
        return 'window_end'

    task.battle_skill_loop = fake_loop
    end = datetime.now() + timedelta(seconds=30)
    task.preset_battle_loop((304, 76), 2, end, ['小鬼'])

    assert len(calls) == 1, calls
    slots, kwargs = calls[0]
    assert slots == [2, 0], '技能位顺序 = 预设优先级，不能被改写'
    assert kwargs['include_attack'] is False, '不点普通攻击（共享默认关）'
    assert kwargs['attack_interval'] == task.NORMAL_ATTACK_INTERVAL
    assert kwargs['max_attempts'] == task.SKILL_MAX_ATTEMPTS
    assert callable(kwargs['check_stop']) and callable(kwargs['on_cast'])
    assert callable(kwargs['on_target_lost']), '目标死了要能快速补锁，别退回外层重锁'
    assert task._battle_deadline == end, '停止条件要能读到本场截止时间'
    print('test_preset_loop_uses_skill_loop OK')


def test_preset_loop_respects_normal_attack_switch():
    task, _ = make_task(YuwaiBattleConfig(use_normal_attack=True))
    calls = {}
    task.acquire_target = lambda names=None: True
    task.battle_plan_slots = lambda: [0]
    task.ui_target_attackable = lambda image=None: True
    task.battle_skill_loop = lambda slots, **kw: calls.update(kw) or 'window_end'
    task.preset_battle_loop((304, 76), 2, datetime.now() + timedelta(seconds=30), ['小鬼'])
    assert calls['include_attack'] is True, '设置里打开后要真的补普攻'
    print('test_preset_loop_respects_normal_attack_switch OK')


def test_preset_loop_falls_back_to_fast_click():
    task, _ = make_task()
    calls = {}
    task.acquire_target = lambda names=None: True
    task.battle_plan_slots = lambda: []
    task.battle_skill_loop = lambda *a, **kw: calls.setdefault('skill_loop', True) or 'timeout'
    task.fast_battle_loop = lambda *a, **kw: calls.setdefault('fast', a)
    task.device = SimpleNamespace(sleep=lambda seconds: None, image=None)

    class FastCfg:
        attack_interval = 0.1

    task.preset_battle_loop((304, 76), 2, datetime.now() + timedelta(seconds=30),
                            ['小鬼'], FastCfg())
    assert 'fast' in calls, '一个技能位都认不出来时必须退回快打，不能干站着'
    assert 'skill_loop' not in calls
    print('test_preset_loop_falls_back_to_fast_click OK')


# ------------------------------------------------------------------ 目标死后快速补锁
def _relock_task(attackable=True, name='小鬼'):
    task, battle = make_task()
    task.screenshot = lambda: object()
    task.ui_target_attackable = lambda image=None: attackable
    task.ui_target_name = lambda scales=None, min_score=None: name
    task.C_TARGET_BUTTON_CLICK = SimpleNamespace(coord=lambda: (1242, 280))
    clicks = []
    task.device = SimpleNamespace(
        sleep=lambda seconds: None,
        click=lambda x, y, control_name=None: clicks.append((x, y)),
        click_record_clear=lambda: None,
        image=None)
    return task, clicks


def test_relock_target_clicks_and_verifies_name():
    """补锁：点一次固定坐标的「目标」+ 判可攻击 + 读名字，一次就命中"""
    task, clicks = _relock_task()
    assert task.relock_target(['小鬼', '鬼王']) is True
    assert clicks == [(1242, 280)], clicks
    print('test_relock_target_clicks_and_verifies_name OK')


def test_relock_target_skips_player():
    """锁到玩家（名字不在白名单）要继续切，不能开打"""
    task, clicks = _relock_task(name='某某玩家')
    assert task.relock_target(['小鬼']) is False
    assert len(clicks) == task.RELOCK_MAX_SWITCH, '切满上限交给完整流程'
    assert task.RELOCK_MAX_SWITCH >= 2
    print('test_relock_target_skips_player OK')


def test_relock_target_retries_on_corpse():
    """第一下锁到刚死的地尸（判不过可攻击），第二下切到活怪"""
    task, clicks = _relock_task()
    checks = iter([False, True])
    task.ui_target_attackable = lambda image=None: next(checks, False)
    assert task.relock_target(['小鬼']) is True
    assert len(clicks) == 2, clicks
    print('test_relock_target_retries_on_corpse OK')


def test_relock_target_gives_up_when_nothing_locked():
    task, clicks = _relock_task(attackable=False)
    assert task.relock_target(['小鬼']) is False
    assert len(clicks) == task.RELOCK_MAX_SWITCH, clicks
    print('test_relock_target_gives_up_when_nothing_locked OK')


def test_relock_target_without_whitelist_skips_name():
    """白名单留空：只要求可攻击，不读名字"""
    task, clicks = _relock_task()
    task.ui_target_name = lambda scales=None, min_score=None: (_ for _ in ()).throw(
        AssertionError('白名单为空时不该读名字'))
    assert task.relock_target([]) is True
    assert clicks == [(1242, 280)]
    print('test_relock_target_without_whitelist_skips_name OK')


def test_relock_target_uses_config_whitelist():
    """不传 names 时用配置里的战斗目标"""
    task, clicks = _relock_task()
    got = []
    task.battle_relock_target = lambda names=None: got.append(names) or True
    assert task.relock_target() is True
    assert got == [['小鬼', '鬼王', '鬼将']], got
    assert clicks == []
    print('test_relock_target_uses_config_whitelist OK')


def test_fast_relock_switch_off_uses_normal_flow():
    """关掉 fast_relock（单 boss / 刷怪间隔 5s+）：目标丢了就按原逻辑结束循环"""
    task, _ = make_task(YuwaiBattleConfig(fast_relock=False))
    called = []
    task.battle_relock_target = lambda names=None: called.append(names) or True
    handler = task.battle_on_target_lost(['小鬼'])
    assert handler() is False, '没开补锁时不能接管循环'
    assert called == [], '开关关掉就不该去点目标'

    # 打开时才真的补锁
    task2, _ = make_task(YuwaiBattleConfig(fast_relock=True))
    task2.battle_relock_target = lambda names=None: True
    assert task2.battle_on_target_lost(['小鬼'])() is True
    print('test_fast_relock_switch_off_uses_normal_flow OK')


def test_fast_loop_prefers_fast_relock():
    task, _ = make_task(YuwaiBattleConfig(fast_click=True))
    calls = {'relock': 0, 'acquire': 0}

    class _Stop(Exception):
        pass

    def relock(names=None):
        calls['relock'] += 1
        return True

    def fast_click_once():
        raise _Stop()

    task.battle_relock_target = relock
    task.acquire_target = lambda names=None: calls.__setitem__('acquire', calls['acquire'] + 1)
    task.fast_click_once = fast_click_once
    try:
        task.fast_battle_loop((304, 76), 2, 0.1, datetime.now() + timedelta(seconds=30), ['小鬼'])
    except _Stop:
        pass
    assert calls == {'relock': 1, 'acquire': 0}, calls
    print('test_fast_loop_prefers_fast_relock OK')


# ------------------------------------------------------------------ 秒杀快打
def test_fast_loop_uses_fixed_sequence():
    task, battle = make_task(YuwaiBattleConfig(fast_click=True))
    clicks = []

    class _Stop(Exception):
        pass

    def fast_click_once():
        clicks.append(1)
        if len(clicks) == 3:
            raise _Stop()

    task.battle_relock_target = lambda names=None: False
    task.acquire_target = lambda names=None: True
    task.fast_click_once = fast_click_once
    end = datetime.now() + timedelta(seconds=60)
    try:
        task.fast_battle_loop((304, 76), 2, 0.1, end, ['小鬼'])
        assert False, '应该被 _Stop 打断（循环本身会打到时段结束）'
    except _Stop:
        pass
    assert len(clicks) == 3, clicks
    print('test_fast_loop_uses_fixed_sequence OK')


def test_fast_click_once_order():
    task, _ = make_task()
    seq = []
    name = {'battle_click_skill': 'skill', 'battle_click_attack': 'attack'}
    for method in name:
        setattr(task, method, (lambda key: lambda index=None: seq.append(
            (key, index) if index is not None else (key,)))(name[method]))
    task.fast_click_once()
    assert [item[0] for item in seq] == ['skill', 'skill', 'attack',
                                         'skill', 'skill', 'skill', 'skill', 'attack'], seq
    assert [item[1] for item in seq if item[0] == 'skill'] == [0, 0, 0, 0, 2, 2], seq
    print('test_fast_click_once_order OK')


# ------------------------------------------------------------------ 停止条件
def test_stop_reason_only_window_end():
    """怪物在时段内一直刷新，所以只有时段到点才停，不存在「清完了」"""
    task, _ = make_task()
    task.reset_battle_progress(datetime.now() + timedelta(minutes=10))
    assert task.battle_stop_reason() is None, '时段没到就继续打'

    task.reset_battle_progress(datetime.now() - timedelta(seconds=1))
    assert task.battle_stop_reason() == 'window_end', '11:30 / 16:30 到点必须收工'
    print('test_stop_reason_only_window_end OK')


# ------------------------------------------------------------------ 离开迷窟
def _leave_task(pan_ok=True, open_ok=True, left_results=None, already_left=False):
    """造一个只用来测 leave_mimang 的对象：记录点击点 / 拖动角落 / 开图次数"""
    task, _ = make_task()
    clicks, pans, opens = [], [], []
    task.map_current_location = lambda image=None: (
        ('沼泽', 100, 100) if already_left else None)

    def open_map(timeout=8):
        opens.append(1)
        return open_ok

    task.map_open_minimap = open_map
    task.map_pan_to_corner = lambda corner, **kw: pans.append(corner) or pan_ok
    task.map_close_minimap = lambda **kw: True
    task.device = SimpleNamespace(
        sleep=lambda seconds: None,
        click=lambda x, y, control_name=None: clicks.append((x, y)),
        click_record_clear=lambda: None,
        image=None)
    results = list(left_results or [])
    task.wait_leave_mimang = lambda timeout=None: results.pop(0) if results else False
    return task, clicks, pans, opens


def test_leave_mimang_tries_exits_in_order():
    """第一个出口没走出去 -> 再开图拖到对应角落点第二个 -> 第三个"""
    task, clicks, pans, opens = _leave_task(left_results=[False, False, True])
    assert task.leave_mimang(SimpleNamespace(relay_location='沼泽')) is True

    expected = [(704 + 31 // 2, 134 + 32 // 2),
                (233 + 31 // 2, 411 + 32 // 2),
                (247 + 31 // 2, 571 + 32 // 2)]
    assert clicks == expected, clicks
    # 1、2 号在右上角视图里，3 号要拖到底部才看得到
    assert pans == ['top_right', 'top_right', 'bottom_right'], pans
    assert len(opens) == 3, opens
    print('test_leave_mimang_tries_exits_in_order OK')


def test_leave_mimang_gives_up_after_three():
    task, clicks, pans, _ = _leave_task(left_results=[False, False, False])
    assert task.leave_mimang(SimpleNamespace(relay_location='沼泽')) is False
    assert len(clicks) == len(task.LEAVE_EXITS) == 3
    assert pans == ['top_right', 'top_right', 'bottom_right']
    print('test_leave_mimang_gives_up_after_three OK')


def test_leave_mimang_skips_when_already_out():
    """上一轮其实已经出去了：别再在主页面瞎点"""
    task, clicks, pans, opens = _leave_task(already_left=True)
    assert task.leave_mimang(SimpleNamespace(relay_location='沼泽')) is True
    assert clicks == [] and pans == [] and opens == []
    print('test_leave_mimang_skips_when_already_out OK')


def test_leave_mimang_returns_false_when_minimap_broken():
    task, clicks, _, _ = _leave_task(open_ok=False, left_results=[False])
    assert task.leave_mimang(SimpleNamespace(relay_location='沼泽')) is False
    assert clicks == [], '小地图打不开就别瞎点'
    print('test_leave_mimang_returns_false_when_minimap_broken OK')


def test_wait_leave_mimang_detects_map_change():
    task, _ = make_task()
    task.map_move_read_pos = lambda: ('沼泽', 10, 20)
    assert task.wait_leave_mimang(timeout=1) is True

    task.map_move_read_pos = lambda: ('域外迷窟', 304, 76)
    assert task.wait_leave_mimang(timeout=0.2) is False
    print('test_wait_leave_mimang_detects_map_change OK')


# ------------------------------------------------------------------ 分发
def _window_task(fast_click: bool, leave_ok=True, raise_inside=None):
    task, battle = make_task(YuwaiBattleConfig(fast_click=fast_click))
    task.parse_coord = lambda text: (304, 76)
    task.recover_position = lambda coord, tolerance, timeout=None: True
    task.ui_close_menu = lambda: None
    task.battle_target_names = lambda: ['小鬼']

    class Cfg:
        battle_coord = '304,76'
        battle_tolerance = 2
        attack_interval = 0.1

    calls = {'preset': 0, 'fast': 0, 'leave': 0}
    task.preset_battle_loop = lambda *a, **kw: calls.__setitem__('preset', calls['preset'] + 1)
    task.fast_battle_loop = lambda *a, **kw: calls.__setitem__('fast', calls['fast'] + 1)

    def leave(cfg):
        calls['leave'] += 1
        return leave_ok

    task.leave_mimang = leave
    if raise_inside:
        setattr(task, f'{raise_inside}_battle_loop',
                lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom')))
    return task, Cfg(), calls


def test_handle_window_dispatch():
    task, cfg, calls = _window_task(fast_click=False)
    task.handle_window([], (datetime.now(), datetime.now() + timedelta(minutes=5)),
                       cfg, deadline=datetime.now() + timedelta(seconds=5))
    assert calls['preset'] == 1 and calls['fast'] == 0, calls
    assert calls['leave'] == 1, '收工必须离开迷窟'

    task, cfg, calls = _window_task(fast_click=True)
    task.handle_window([], (datetime.now(), datetime.now() + timedelta(minutes=5)),
                       cfg, deadline=datetime.now() + timedelta(seconds=5))
    assert calls['fast'] == 1 and calls['preset'] == 0, calls
    assert calls['leave'] == 1
    print('test_handle_window_dispatch OK')


def test_handle_window_always_leaves():
    task, cfg, calls = _window_task(fast_click=False, raise_inside='preset')
    try:
        task.handle_window([], (datetime.now(), datetime.now() + timedelta(minutes=5)),
                           cfg, deadline=datetime.now() + timedelta(seconds=5))
        assert False, '异常应该继续往外抛'
    except RuntimeError:
        pass
    assert calls['leave'] == 1, '异常路径也必须离开迷窟'
    print('test_handle_window_always_leaves OK')


def main() -> int:
    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith('test_') or not callable(func):
            continue
        try:
            func()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {name}: {e}')
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f'ERROR {name}: {type(e).__name__}: {e}')
    print('all passed' if not failed else f'{failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
