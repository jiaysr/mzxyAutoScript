# -*- coding: utf-8 -*-
"""
副本选中态特征真机自检（只点左侧行，不点传送/扫荡/重置）

逐行点击左侧副本 -> 等右侧详情刷新 -> 匹配录制的 Boss 特征图，
输出每行的「特征命中 / OCR 文本 / 重置按钮是否可见」。

用法：
    toolkit\\python.exe tests\\dungeon_feature_probe.py
"""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

ROW_YS = (220, 301, 382, 462, 542)
ROW_X = 300
SCREEN_COUNT = 3


def main() -> int:
    parser = argparse.ArgumentParser(description='副本选中态特征自检')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--output', default='log/dungeon_feature_probe.json')
    args = parser.parse_args()

    from module.atom.ocr import RuleOcr
    from module.config.config import Config
    from module.device.device import Device
    from tasks.Dungeon.common import DungeonBase
    from tasks.Dungeon.counts import parse_dungeon_rows

    config = Config(args.config)
    device = Device(config)
    task = DungeonBase(config, device)
    if not device.app_is_running():
        print('游戏未运行，退出', flush=True)
        return 1

    features = [
        (task.I_SHAYU_MULTI_FEATURE, '沙狱[多人]'),
        (task.I_SHAYU_SOLO_FEATURE, '沙狱[单人]'),
        (task.I_DIYULING_MULTI_FEATURE, '帝玉岭[多人]'),
        (task.I_DIYULING_SOLO_FEATURE, '帝玉岭[单人]'),
        (task.I_LIULI_FEATURE, '琉璃幻境'),
        (task.I_XIANJU_FEATURE, '仙居福地'),
        (task.I_TIANYUAN_FEATURE, '天渊灵岛'),
        (task.I_ZHIZUN_FEATURE, '至尊巅峰'),
    ]

    if not task.dungeon_open_page():
        raise RuntimeError('dungeon page does not appear')

    result = []
    for screen in range(SCREEN_COUNT):
        for y in ROW_YS:
            task.device.click(x=ROW_X, y=y, control_name=f'dungeon_row_{y}')
            task.device.click_record_clear()
            task.device.sleep(0.5)
            task.screenshot()
            image = task.device.image

            scores = []
            for rule, name in features:
                source = rule.corp(image)
                if source is None or source.size == 0 or rule.image is None:
                    continue
                if (rule.image.shape[0] > source.shape[0]
                        or rule.image.shape[1] > source.shape[1]):
                    continue
                score = float(cv2.matchTemplate(
                    source, rule.image, cv2.TM_CCOEFF_NORMED).max())
                scores.append((round(score, 3), name))
            scores.sort(reverse=True)

            roi = (112, y - 35, 400, 70)
            rule = RuleOcr(roi=roi, area=roi, mode='Full', method='Default',
                           keyword='', name=f'dungeon_row_{y}')
            items = []
            for item in rule.detect_and_ocr(image, logDisplay=False):
                ys = [point[1] for point in item.box]
                items.append((item.ocr_text, (min(ys) + max(ys)) / 2 + roi[1]))
            row = parse_dungeon_rows(items)

            reset_ready = task.I_DUNGEON_RESET_BUTTON.match(image)
            result.append({
                'screen': screen,
                'y': y,
                'best': scores[0] if scores else None,
                'scores': scores[:3],
                'ocr': row[:1],
                'reset_ready': reset_ready,
            })
            print(f'screen={screen} y={y} best={scores[0] if scores else None} '
                  f'ocr={row[:1]} reset={reset_ready}', flush=True)
        if screen < SCREEN_COUNT - 1:
            task.ui_swipe_gentle((300, 520), (300, 280), steps=3, step_delay=0.25)
            task.device.sleep(1.0)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'-> {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
