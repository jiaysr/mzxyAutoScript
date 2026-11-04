# -*- coding: utf-8 -*-
"""
小地图箭头模板真机对比：多地图检查模板匹配 vs 原颜色判断

用法：
    toolkit\\python.exe tests\\minimap_arrows_live.py
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

MAPS = ['牧野', '万剑冢', '沼泽', '骨牢', '忘川', '域外灵岛']


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
    for name in MAPS:
        entry = {'map': name}
        entry['teleport'] = task.map_teleport(name)
        task.device.sleep(1.0)
        task.map_close_main_popup()
        if not task.map_open_minimap():
            entry['minimap'] = False
            results.append(entry)
            continue
        task.screenshot()
        image = task.device.image
        template = {}
        for side, rule in (
                ('top', task.I_ITEM_7), ('left', task.I_ITEM_8),
                ('bottom', task.I_ITEM_9), ('right', task.I_ITEM_10)):
            template[side] = bool(rule.match(image))
        color = sorted(task._map_minimap_arrows_color(image))
        actual = sorted(task.map_minimap_arrows())
        entry.update({
            'minimap': True,
            'template': [s for s, ok in template.items() if ok],
            'color': color,
            'used': actual,
        })
        shot = f'log/minimap_arrows_{name}.png'
        cv2.imwrite(shot, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        entry['screenshot'] = shot
        task.map_close_minimap()
        results.append(entry)
        print(f'{name}: template={entry["template"]} color={entry["color"]}', flush=True)

    with open('log/minimap_arrows_live.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print('-> log/minimap_arrows_live.json', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
