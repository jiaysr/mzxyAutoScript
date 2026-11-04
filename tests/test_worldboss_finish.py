# -*- coding: utf-8 -*-
"""
世界首领结束模板单测：每个 boss 的模板只能命中自己的击杀消息

结束模板都是聊天栏截取，彼此只差 boss 名，普通 0.8 阈值会互相命中；
这里验证提高阈值后模板仍然可区分。

    toolkit\\python.exe tests\\test_worldboss_finish.py
"""
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.GlobalGame.assets import GlobalGameAssets as GG
from tasks.WorldBoss.assets import WorldBossAssets as W
from tasks.WorldBoss.script_task import ScriptTask

FINISH_RULES = {
    'manchui': W.I_FINISH_MANCHUI,
    'wuqing': W.I_FINISH_WUQING,
    'julian': W.I_FINISH_JULIAN,
    'fuwang': W.I_FINISH_FUWANG,
    'xiewang': W.I_FINISH_XIEWANG,
}
FINISH_POS = (1, 641)


def scene_with(rule) -> np.ndarray:
    """把结束模板贴到聊天栏位置，构造一帧 1280x720 截图"""
    template = rule.image
    x, y = FINISH_POS
    image = np.zeros((720, 1280, 3), dtype=template.dtype)
    image[y:y + template.shape[0], x:x + template.shape[1]] = template
    return image


def test_finish_templates_are_discriminating():
    for key, rule in FINISH_RULES.items():
        image = scene_with(rule)
        hits = [other.name for other in FINISH_RULES.values() if other.match(image)]
        assert rule.name in hits, f'{key} 的模板没有命中自己的消息'
        assert len(hits) == 1, f'{key} 的消息同时命中了 {hits}'


def test_finish_rule_names_follow_boss_key():
    for key in FINISH_RULES:
        assert getattr(W, f'I_FINISH_{key.upper()}') is FINISH_RULES[key]


def test_boss_reward_template():
    """首领归属奖弹窗模板能命中自身，空白图不命中"""
    rule = W.I_BOSS_REWARD
    template = rule.image
    image = np.zeros((720, 1280, 3), dtype=template.dtype)
    x, y = rule.roi_front[0], rule.roi_front[1]
    image[y:y + template.shape[0], x:x + template.shape[1]] = template
    assert rule.match(image)
    assert not rule.match(np.zeros((720, 1280, 3), dtype=template.dtype))


def test_boss_reward_close_area():
    assert tuple(W.C_BOSS_REWARD_CLOSE.roi_front) == (609, 525, 64, 35)


def test_boss_reward_moved_out_of_global_popup():
    """首领归属奖已从全局弹窗迁到 WorldBoss，全局不应再自动配对处理"""
    assert not hasattr(GG, 'I_POPUP_BOSS_REWARD')
    assert not hasattr(GG, 'C_POPUP_BOSS_REWARD_CLOSE')


# ---------------------------------------------------------------- 目标丢失后的处理
def make_attack_task(loop_result='target_lost', finish_found=False):
    """只实现 attack_boss 需要的成员，不连设备"""
    task = ScriptTask.__new__(ScriptTask)
    task.screenshot = lambda: None
    task.battle_plan_slots = lambda: []
    task.battle_skill_loop = lambda *args, **kwargs: loop_result
    task.wait_finish_template = lambda *args, **kwargs: finish_found
    return task


def test_target_lost_without_finish_returns_target_lost():
    """目标消失且 30s 内没结束消息：不按已结束收尾，返回 target_lost 让外层重锁"""
    task = make_attack_task(loop_result='target_lost', finish_found=False)
    deadline = datetime.now() + timedelta(minutes=5)
    result = task.attack_boss({'name': '斧王'}, object(), deadline)
    assert result == 'target_lost', result


def test_target_lost_with_finish_returns_finished():
    """目标消失但补到了结束消息：照常按 finished 收尾"""
    task = make_attack_task(loop_result='target_lost', finish_found=True)
    deadline = datetime.now() + timedelta(minutes=5)
    result = task.attack_boss({'name': '斧王'}, object(), deadline)
    assert result == 'finished', result


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
