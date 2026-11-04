# -*- coding: utf-8 -*-
"""
沙狱[单人] 真机自检：完整跑一次副本流程

用法：
    toolkit\\python.exe tests\\dungeon_shayu_solo_live.py
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
    parser = argparse.ArgumentParser(description='沙狱单人真机自检')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--output', default='log/dungeon_shayu_solo_live.json')
    args = parser.parse_args()

    from module.config.config import Config
    from module.device.device import Device
    from tasks.Dungeon.dungeons.ShayuSolo import ShayuSolo

    config = Config(args.config)
    device = Device(config)
    task = ShayuSolo(config, device)
    if not device.app_is_running():
        print('游戏未运行，退出', flush=True)
        return 1

    result = {'start': location_text(task)}
    result['run_ok'] = task.run_once()
    result['after'] = location_text(task)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['run_ok'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
