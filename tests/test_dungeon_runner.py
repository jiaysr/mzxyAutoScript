# -*- coding: utf-8 -*-
"""
副本挑战状态机单测（不连设备）

    python tests\\test_dungeon_runner.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Dungeon.counts import FIELD_NAMES
from tasks.Dungeon.runner import DungeonAborted, DungeonRunner


class FakeGame:
    """模拟副本页：run_once 默认不自动扣次数，reset 才提交 1 次"""

    def __init__(self, counts, privilege, auto_decrement=False,
                 privilege_auto_decrement=False):
        self.counts = dict(counts)
        self.privilege = privilege
        self.auto_decrement = auto_decrement
        self.privilege_auto_decrement = privilege_auto_decrement
        self.selected = None
        self.name_to_field = {}
        self.actions = []
        self._pending_privilege = False

    def status(self):
        return {'counts': dict(self.counts), 'privilege': self.privilege}

    def select(self, name):
        self.actions.append(('select', name))
        self.selected = name
        return True

    def reset(self):
        field = self.name_to_field[self.selected]
        self.actions.append(('reset', field))
        if self.counts.get(field, 0) > 0:
            self.counts[field] -= 1
        elif self._pending_privilege and self.privilege > 0:
            self.privilege -= 1
        self._pending_privilege = False
        return True

    def run_once(self, field):
        self.actions.append(('run', field))
        if self.counts.get(field, 0) > 0:
            if self.auto_decrement:
                self.counts[field] -= 1
        else:
            self._pending_privilege = True
            if self.privilege_auto_decrement and self.privilege > 0:
                self.privilege -= 1
        return True


def make_runner(game, targets, privilege_field, field_status=None):
    game.name_to_field = {target['name']: target['field'] for target in targets}
    return DungeonRunner(
        targets=targets,
        privilege_field=privilege_field,
        status=game.status,
        select=game.select,
        reset=game.reset,
        run_once=game.run_once,
        field_status=field_status,
    )


A_FIELD, B_FIELD = 'shayu_multi', 'shayu_solo'
A_NAME, B_NAME = FIELD_NAMES[A_FIELD], FIELD_NAMES[B_FIELD]
A = {'field': A_FIELD, 'name': A_NAME, 'configured': 2}
B = {'field': B_FIELD, 'name': B_NAME, 'configured': 1}


def test_normal_two_runs_then_privilege_then_other():
    game = FakeGame({A_FIELD: 2, B_FIELD: 1}, privilege=1)
    make_runner(game, [A, B], privilege_field=A_FIELD).run()

    assert game.counts == {A_FIELD: 0, B_FIELD: 1}
    assert game.privilege == 0
    # 跑副本前不选中；只有重置提交时才点左侧列表
    assert game.actions == [
        ('run', A_FIELD),
        ('select', A_NAME), ('reset', A_FIELD),
        ('run', A_FIELD),
        ('select', A_NAME), ('reset', A_FIELD),
        ('run', A_FIELD),
        ('select', A_NAME), ('reset', A_FIELD),
        ('run', B_FIELD),
    ]


def test_leftover_normal_count_reset_before_privilege():
    game = FakeGame({A_FIELD: 1}, privilege=1)
    make_runner(game, [A], privilege_field=A_FIELD).run()

    assert game.counts == {A_FIELD: 0}
    assert game.privilege == 0
    # 正常只打 1 次（剩余 1），不需要重置；特权开始前先把剩余 1 次重置掉
    assert game.actions == [
        ('run', A_FIELD),
        ('select', A_NAME), ('reset', A_FIELD),
        ('run', A_FIELD),
        ('select', A_NAME), ('reset', A_FIELD),
    ]


def test_auto_decrement_needs_no_reset():
    game = FakeGame({A_FIELD: 2, B_FIELD: 1}, privilege=0, auto_decrement=True)
    make_runner(game, [A, B], privilege_field=None).run()

    assert game.counts == {A_FIELD: 0, B_FIELD: 0}
    assert all(action[0] != 'reset' for action in game.actions)
    assert game.actions == [
        ('run', A_FIELD),
        ('run', A_FIELD),
        ('run', B_FIELD),
    ]


def test_field_status_reads_only_current_after_run():
    game = FakeGame({A_FIELD: 2}, privilege=0, auto_decrement=True)
    calls = {'status': 0, 'field': 0}

    def status():
        calls['status'] += 1
        return game.status()

    def field_status(field):
        calls['field'] += 1
        return game.counts.get(field, 0), game.privilege

    runner = DungeonRunner(
        targets=[A], privilege_field=None,
        status=status, select=game.select, reset=game.reset,
        run_once=game.run_once, field_status=field_status,
    )
    runner.run()

    assert calls['status'] == 1  # 只在开始时全量识别一次
    assert calls['field'] >= 2   # 每次跑完只读当前副本


def test_aborted_dungeon_skips_remaining_and_privilege():
    game = FakeGame({A_FIELD: 2, B_FIELD: 1}, privilege=1)
    calls = []

    def run_once(field):
        calls.append(field)
        if field == A_FIELD:
            raise DungeonAborted('died in dungeon')
        return game.run_once(field)

    runner = DungeonRunner(
        targets=[A, B], privilege_field=A_FIELD,
        status=game.status, select=game.select, reset=game.reset,
        run_once=run_once,
    )
    runner.run()

    assert calls == [A_FIELD, B_FIELD]  # A 只尝试一次，剩余次数和特权都跳过
    assert game.privilege == 1          # 特权阶段没有执行


def test_privilege_auto_decrement_needs_no_reset():
    game = FakeGame({A_FIELD: 0}, privilege=2, privilege_auto_decrement=True)
    make_runner(game, [A], privilege_field=A_FIELD).run()

    assert game.privilege == 0
    assert all(action[0] != 'reset' for action in game.actions)
    assert [action for action in game.actions if action[0] == 'run'] == [
        ('run', A_FIELD), ('run', A_FIELD),
    ]


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'---- {len(tests)}/{len(tests)} passed ----')
