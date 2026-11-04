# -*- coding: utf-8 -*-
"""
小地图视口平移真机测试：多地图依次移动到四个角落，记录模板箭头变化

用法：
    toolkit\\python.exe tests\\minimap_pan_live.py
"""
import json
import os
import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

MAPS = ['牧野', '沼泽', '忘川']
CORNERS = ['top_left', 'top_right', 'bottom_right', 'bottom_left']


def main() -> int:
    from module.config.config import Config
    from module.device.device import Device
    from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing

    config = Config('oas1')
    device = Device(config)
    task = LiuliHuanjing(config, device)
    if not device.app_is_running():
        print('游戏未运行', flush=True)
        return 1

    results = []
    for map_name in MAPS:
        entry = {'map': map_name, 'teleport': task.map_teleport(map_name), 'corners': []}
        results.append(entry)
        if not entry['teleport']:
            continue
        task.device.sleep(1.0)
        task.map_close_main_popup()
        if not task.map_open_minimap():
            entry['minimap'] = False
            continue
        entry['minimap'] = True
        try:
            for corner in CORNERS:
                before = sorted(task.map_minimap_arrows())
                ok = task.map_pan_to_corner(corner, max_drags=10)
                task.screenshot()
                after = sorted(task.map_minimap_arrows())
                shot = f'log/minimap_pan_{map_name}_{corner}.png'
                cv2.imwrite(shot, cv2.cvtColor(task.device.image, cv2.COLOR_RGB2BGR))
                entry['corners'].append({
                    'corner': corner, 'ok': ok,
                    'before': before, 'after': after, 'screenshot': shot,
                })
                print(f'{map_name} {corner}: {before} -> {after} ok={ok}', flush=True)
                with open('log/minimap_pan_live.json', 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
        finally:
            task.map_close_minimap()

    with open('log/minimap_pan_live.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print('-> log/minimap_pan_live.json', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
