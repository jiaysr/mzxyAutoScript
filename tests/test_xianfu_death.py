# -*- coding: utf-8 -*-
"""
仙府九重天死亡恢复单测（不连设备）

覆盖：
- 读背包碎片中途死亡：重试时重读（因为没读完）
- 背包统计成功后再死亡：不重扫背包，counts 保留
- 战斗中死亡：继续打下一场，battles 不重复计数
- 连续死亡超过上限：放弃本次运行（TaskEnd），仍会重排下次时间

    toolkit\\python.exe tests\\test_xianfu_death.py
"""
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.config.config import Config
from module.exception import TaskEnd
from tasks.XianfuJiuchongtian.script_task import ScriptTask, XianfuDeath


def make_task() -> ScriptTask:
    device = SimpleNamespace(image=None, sleep=lambda seconds: None)
    return ScriptTask(Config('oas1'), device)


def patch(task: ScriptTask, holder: dict, stats: dict) -> None:
    """把 run() 里除死亡处理外的设备操作全部打桩"""
    task.load_config = lambda: (1, holder.get('threshold', 10), 0, 0)
    task.enter_xianfu_page = lambda *a, **k: True
    task.bag_repair_equipment = lambda: stats.setdefault('repair', 0) or True
    task.schedule_next_run = lambda *a, **k: stats.__setitem__('scheduled', True)

    def fake_stone():
        stats['stone_reads'] = stats.get('stone_reads', 0) + 1
        return holder['stone']

    def fake_bag(color):
        stats['bag_calls'] = stats.get('bag_calls', 0) + 1
        if stats['bag_calls'] <= holder.get('bag_fail_times', 0):
            raise XianfuDeath('bag_count')
        return {1: 0, 2: 0, 3: 0, 4: 0}

    def fake_battle(daguan, xiaoguan):
        stats['battles'] = stats.get('battles', 0) + 1
        if stats['battles'] <= holder.get('battle_fail_times', 0):
            raise XianfuDeath('battle')
        holder['stone'] -= 4          # 成功一场消耗 4 点仙石
        task.last_reward = ('南极', '一')
        return True

    task.read_stone_ready = fake_stone
    task.bag_piece_counts = fake_bag
    task.run_one_battle = fake_battle


def run_flow(holder: dict) -> tuple:
    task = make_task()
    stats = {}
    patch(task, holder, stats)
    try:
        task.run()
    except TaskEnd as e:
        return e, stats
    return None, stats


def test_bag_retry_after_death():
    """背包统计第一次死亡：重试后重读一次，最终能拿到 counts 并开打"""
    holder = {'stone': 12, 'bag_fail_times': 1}
    _, stats = run_flow(holder)
    assert stats['bag_calls'] == 2, stats
    assert stats['battles'] >= 1, stats


def test_bag_not_rescanned_after_death():
    """背包统计成功后，战斗中死亡不会触发重扫背包"""
    holder = {'stone': 12, 'battle_fail_times': 1}
    _, stats = run_flow(holder)
    assert stats['bag_calls'] == 1, stats
    assert stats['battles'] >= 2, stats          # 第一次死在战斗里，之后继续打


def test_battle_death_does_not_double_count():
    """战斗中死亡不消耗仙石、不计入 battles，继续打下一场"""
    holder = {'stone': 8, 'threshold': 4, 'battle_fail_times': 1}
    _, stats = run_flow(holder)
    # 8 点够 2 场：第 1 次死在战斗里（不扣仙石），之后 2 场成功把仙石打完
    assert stats['battles'] == 3, stats
    assert holder['stone'] == 0, holder


def test_give_up_after_max_deaths():
    """连续死亡超过上限：放弃本次运行，但仍重排下次时间"""
    holder = {'stone': 12, 'battle_fail_times': 99}
    exc, stats = run_flow(holder)
    assert isinstance(exc, TaskEnd), exc
    assert stats['battles'] == ScriptTask.MAX_DEATH_RETRY, stats
    assert stats.get('scheduled') is True, stats


def test_stone_below_threshold_skips():
    """仙石不够直接退出，不读背包也不修理"""
    holder = {'stone': 2, 'threshold': 10}
    exc, stats = run_flow(holder)
    assert isinstance(exc, TaskEnd), exc
    assert 'bag_calls' not in stats, stats
    assert stats.get('scheduled') is True, stats


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
