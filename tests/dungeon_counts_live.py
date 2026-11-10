# -*- coding: utf-8 -*-
"""
副本次数真机识别自检（只读，不点副本）

直接调用 DungeonBase.dungeon_scan_counts()：进活动-副本页 -> 复位列表 ->
分段慢滑 + 每屏 OCR -> 输出全部副本的剩余次数。

用法：
    toolkit\\python.exe tests\\dungeon_counts_live.py
    toolkit\\python.exe tests\\dungeon_counts_live.py --config oas1 --output log/dungeon_counts.json
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


def main() -> int:
    parser = argparse.ArgumentParser(description='副本次数真机识别自检')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--output', default='log/dungeon_counts.json')
    args = parser.parse_args()

    from module.config.config import Config
    from module.device.device import Device
    from tasks.Dungeon.common import DungeonBase

    config = Config(args.config)
    device = Device(config)
    task = DungeonBase(config, device)
    print(f'设备: {device.serial} 包名: {device.package}', flush=True)
    if not device.app_is_running():
        print('游戏未运行，退出', flush=True)
        return 1

    counts = task.dungeon_scan_counts()
    from tasks.Dungeon.counts import config_counts
    mapped = config_counts(counts)

    result = {'counts': counts, 'config_counts': mapped}
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'共识别 {len(counts)} 个副本 -> {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
