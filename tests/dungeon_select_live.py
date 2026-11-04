# -*- coding: utf-8 -*-
"""
副本切换真机自检：点击左侧列表 -> 右侧特征校验选中态

会依次切到已录制特征的副本，每次都会先关闭活动弹窗重进（复位列表）。
只点左侧行，不点传送/寻路/扫荡/重置。

用法：
    toolkit\\python.exe tests\\dungeon_select_live.py
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

TARGETS = [
    '沙狱[多人]', '沙狱[单人]',
    '帝玉岭[多人]', '帝玉岭[单人]',
    '琉璃幻境', '仙居福地', '天渊灵岛', '至尊巅峰',
]


def main() -> int:
    parser = argparse.ArgumentParser(description='副本切换真机自检')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--output', default='log/dungeon_select_live.json')
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

    results = []
    for name in TARGETS:
        ok = task.dungeon_select(name)
        selected = task.dungeon_selected_name()
        results.append({'target': name, 'select_ok': ok, 'selected': selected})
        print(f'{name} -> select={ok} selected={selected}', flush=True)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'-> {args.output}', flush=True)
    return 0 if all(item['select_ok'] for item in results) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
