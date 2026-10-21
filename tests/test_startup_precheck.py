# -*- coding: utf-8 -*-
"""
调度器启动预检（Script._ensure_game_running_on_startup）的单元测试（不连设备）

覆盖三个分支：
- 游戏在运行            -> 不启动游戏
- 游戏未运行            -> 走 Restart.app_restart()（启动 + 登录）
- 状态检查本身抛异常    -> 只告警跳过，不阻塞调度启动

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_startup_precheck.py
装了 pytest 时也可以：python -m pytest tests/test_startup_precheck.py
"""
import sys
import traceback
import types
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from script import Script


class FakeRestart:
    """替换 tasks.Restart.script_task.ScriptTask，只记录 app_restart 被调用"""

    calls = []

    def __init__(self, config, device):
        self.config = config
        self.device = device

    def app_restart(self):
        FakeRestart.calls.append(True)


def install_fake_restart():
    module = types.ModuleType('tasks.Restart.script_task')
    module.ScriptTask = FakeRestart
    sys.modules['tasks.Restart.script_task'] = module


def make_script(running):
    """构造只够预检用的 Script（绕过 __init__，不连设备）"""
    def app_is_running():
        if isinstance(running, Exception):
            raise running
        return running

    obj = Script.__new__(Script)
    obj.__dict__['device'] = SimpleNamespace(app_is_running=app_is_running)
    obj.config = None
    return obj


def test_running_skips_restart():
    """游戏已在运行：不启动游戏"""
    FakeRestart.calls.clear()
    install_fake_restart()
    make_script(True)._ensure_game_running_on_startup()
    assert FakeRestart.calls == []


def test_not_running_restarts_game():
    """游戏未运行：调用 Restart 启动并登录"""
    FakeRestart.calls.clear()
    install_fake_restart()
    make_script(False)._ensure_game_running_on_startup()
    assert FakeRestart.calls == [True]


def test_check_error_does_not_block():
    """设备层异常（如 atx-agent 失联）：只告警跳过，不抛错、不阻塞启动"""
    FakeRestart.calls.clear()
    install_fake_restart()
    make_script(RuntimeError('atx-agent dead'))._ensure_game_running_on_startup()
    assert FakeRestart.calls == []


def test_restart_failure_does_not_block():
    """启动/登录过程失败：吞掉异常，交给调度流程处理"""
    def boom(self):
        raise RuntimeError('login failed')

    FakeRestart.calls.clear()
    FakeRestart.app_restart = boom
    try:
        install_fake_restart()
        make_script(False)._ensure_game_running_on_startup()
    finally:
        del FakeRestart.app_restart


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
