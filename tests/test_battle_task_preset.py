# -*- coding: utf-8 -*-
"""
「战斗预设选择不生效」回归测试（不连设备）

真机症状：任务「战斗配置」里选了预设，实际打怪还是用系统预设。
根因：共享实现 `BattleAttack.battle_task_preset()` 取的是**任务根节点**的
`battle_preset`，而字段其实在任务的 `battle_config` 组里 —— 取不到就返回空串，
`battle_preset()` 于是一直回落到 `DEFAULT_PRESET_ID`。
老任务（世界首领 / 仙府 / 迷窟 / 不周山 / 副本 / 战车）各自覆盖过这个方法，
新任务不覆盖就会踩；现在统一在共享实现里取。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_task_preset.py
装了 pytest 时也可以：python -m pytest tests/test_battle_task_preset.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.attack import BattleAttack
from tasks.Battle.config import DEFAULT_PRESET_ID
from module.config.config_model import ConfigModel


def make_task(model, command: str):
    """造一个只带 config 的战斗能力对象（battle_task_preset 只看 config）"""
    task = BattleAttack.__new__(BattleAttack)
    config = SimpleNamespace(model=model)
    config.task = SimpleNamespace(command=command) if command else None
    task.config = config
    return task


def test_reads_battle_config_group():
    """字段在 battle_config 组里（默认就是系统预设）"""
    model = ConfigModel()
    task = make_task(model, 'WorldTask')
    assert task.battle_task_preset() == DEFAULT_PRESET_ID, task.battle_task_preset()

    # 用户在任务里换了预设
    model.world_task.battle_config.battle_preset = 'my_team'
    assert task.battle_task_preset() == 'my_team'


def test_reads_other_tasks_own_group():
    """每个任务各读自己的组，不会串台"""
    model = ConfigModel()
    model.world_task.battle_config.battle_preset = 'world_team'
    model.yuwai_mimang.battle_config.battle_preset = 'mimang_team'

    assert make_task(model, 'WorldTask').battle_task_preset() == 'world_team'
    assert make_task(model, 'YuwaiMimang').battle_task_preset() == 'mimang_team'


def test_root_field_fallback():
    """兼容把字段直接放任务根上的自定义任务"""
    model = SimpleNamespace(my_task=SimpleNamespace(battle_preset='root_team'))
    assert make_task(model, 'MyTask').battle_task_preset() == 'root_team'


def test_no_task_context():
    """没有任务上下文（直跑脚本）返回空串，不报错"""
    model = ConfigModel()
    assert make_task(model, '').battle_task_preset() == ''
    assert make_task(model, 'NotExistTask').battle_task_preset() == ''


def test_battle_config_object():
    model = ConfigModel()
    task = make_task(model, 'WorldTask')
    group = task.battle_task_battle_config()
    assert group is not None and hasattr(group, 'use_normal_attack')
    # 同一份对象：改了能读到
    group.use_normal_attack = True
    assert task.battle_use_normal_attack() is True


def main():
    cases = [
        test_reads_battle_config_group,
        test_reads_other_tasks_own_group,
        test_root_field_fallback,
        test_no_task_context,
        test_battle_config_object,
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
