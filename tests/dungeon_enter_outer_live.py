# -*- coding: utf-8 -*-
"""
副本外地图入口真机自检：牧野 -> 沙狱外，万剑冢 -> 帝玉岭外

只走到外地图，不进入具体副本；琉璃幻境/仙居福地不测。
用法：
    toolkit\\python.exe tests\\dungeon_enter_outer_live.py
"""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def location_text(task) -> str:
    loc = task.map_current_location()
    return f'{loc[0]}{loc[1]},{loc[2]}' if loc else 'unknown'


def main() -> int:
    parser = argparse.ArgumentParser(description='副本外地图入口真机自检')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--output', default='log/dungeon_enter_outer.json')
    args = parser.parse_args()

    from module.config.config import Config
    from module.device.device import Device
    from tasks.Dungeon.common import DungeonBase

    config = Config(args.config)
    device = Device(config)
    task = DungeonBase(config, device)
    if not device.app_is_running():
        print('游戏未运行，退出', flush=True)
        return 1

    result = {'start': location_text(task)}
    print(f"start: {result['start']}", flush=True)

    result['shayu_ok'] = task.dungeon_enter_shayu_outer()
    result['after_shayu'] = location_text(task)
    print(f"shayu: {result['shayu_ok']} -> {result['after_shayu']}", flush=True)

    # 从沙狱外直接传万剑冢；如果子地图没有世界地图入口，重启游戏回主页面再试
    from tasks.GameUi.pages.page import page_main
    task.ui_reset_current_page()
    if not task.map_teleport('万剑冢'):
        print('teleport 万剑冢 failed, restart game', flush=True)
        task.ui_restart_game()
        task.ui_reset_current_page()
        task.ui_goto(page_main, timeout=90)
    result['after_diyuling_base'] = location_text(task)
    print(f"base: {result['after_diyuling_base']}", flush=True)

    result['diyuling_ok'] = task.dungeon_enter_diyuling_outer()
    result['after_diyuling'] = location_text(task)
    print(f"diyuling: {result['diyuling_ok']} -> {result['after_diyuling']}", flush=True)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return 0 if result['shayu_ok'] and result['diyuling_ok'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
