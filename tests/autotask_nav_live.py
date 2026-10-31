# -*- coding: utf-8 -*-
"""
AutoTask 基础导航真机自检（不跑任务执行）

流程：连接设备 -> 识别当前页 -> 进队伍页确保单人 -> 进任务特权页读列表
-> 进任务加量页读卡数量 -> 直连回任务特权 -> 返回主页面。

跳过特权判定（check_privilege），不点自动、不进任务详情。
用法（游戏保持运行）：
    toolkit\\python.exe tests/autotask_nav_live.py
    toolkit\\python.exe tests/autotask_nav_live.py --config oas1
"""
import argparse
import os
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoTask 基础导航真机自检（不跑任务）")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 的配置名")
    args = parser.parse_args()

    from module.config.config import Config
    from module.device.device import Device
    from tasks.AutoTask.page import page_bonus, page_privilege
    from tasks.AutoTask.script_task import ScriptTask
    from tasks.GameUi.pages.page import page_main

    config = Config(args.config)
    device = Device(config)
    task = ScriptTask(config, device)
    print(f'设备: {device.serial} 包名: {device.package}', flush=True)
    if not device.app_is_running():
        print('游戏未运行，退出', flush=True)
        return 1

    print(f'[1] 当前页面: {task.ui_get_current_page()}', flush=True)

    task.ensure_no_team(timeout=90)
    print(f'[2] 队伍页 OK（引擎寻路），当前页面: {task.ui_current}', flush=True)

    if not task.ui_goto(page_privilege, timeout=60):
        raise RuntimeError('ui_goto(page_privilege) failed')
    print(f'[3] 任务特权页 OK，当前页面: {task.ui_current}', flush=True)

    tasks = task.read_privilege_tasks()
    print(f'[4] 特权任务列表读取 OK：{len(tasks)} 条', flush=True)
    for name, (done, total) in tasks.items():
        print(f'      {name}: {done}/{total}', flush=True)

    if not task.ui_goto(page_bonus, timeout=60):
        raise RuntimeError('ui_goto(page_bonus) failed')
    print(f'[5] 任务加量页 OK（特权 -> 加量直连），当前页面: {task.ui_current}', flush=True)

    print(f'[6] 加量卡拥有量: {task.read_bonus_counts()}', flush=True)

    if not task.ui_goto(page_privilege, timeout=60):
        raise RuntimeError('ui_goto(page_privilege) failed')
    print(f'[7] 回任务特权页 OK（加量 -> 特权直连），当前页面: {task.ui_current}', flush=True)

    if not task.ui_goto(page_main, timeout=60):
        raise RuntimeError('ui_goto(page_main) failed')
    print(f'[8] 返回主页面 OK（特权页返回按钮），当前页面: {task.ui_current}', flush=True)

    print('基础导航自检完成', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
