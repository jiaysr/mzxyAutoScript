# This Python file uses the following encoding: utf-8
"""
小地图四角平移测试：打开小地图，依次滑到四个角落。

只拖动地图内容，不点击地图点位，不会移动角色。
到达角落的判据：该侧两个边界箭头消失（箭头只是「还有内容」的指示，不可点击）。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\minimap_corners_test.py
    toolkit\\python.exe tests\\minimap_corners_test.py --corner top_right
    toolkit\\python.exe tests\\minimap_corners_test.py --max-drags 6 --settle 1.2

输出：log/minimap_corners/<时间戳>/
"""
import argparse
import os
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

CORNERS = ('bottom_left', 'top_left', 'top_right', 'bottom_right')


def annotate(game, corner: str, ok: bool):
    image = game.device.image.copy()
    arrows = game.map_minimap_arrows()
    x, y, w, h = game.MAP_CONTENT_REGION
    cv2.rectangle(image, (x, y), (x + w - 1, y + h - 1), (0, 255, 255), 2)
    for side, (rx, ry, rw, rh) in game.MAP_ARROW_REGIONS.items():
        color = (0, 255, 0) if side in arrows else (128, 128, 128)
        cv2.rectangle(image, (rx, ry), (rx + rw, ry + rh), color, 2)
        cv2.putText(image, side, (rx, ry - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color, 1, cv2.LINE_AA)
    title = f'{corner}  ok={ok}  arrows={sorted(arrows)}'
    cv2.putText(image, title, (x + 8, y + 26), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR), arrows


def parse_args():
    parser = argparse.ArgumentParser(description='小地图四角平移测试（不移动角色）')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--corner', choices=CORNERS, default=None,
                        help='只测一个角落；默认四个都测')
    parser.add_argument('--max-drags', type=int, default=4)
    parser.add_argument('--settle', type=float, default=1.0)
    parser.add_argument('--save-dir', type=Path, default=ROOT / 'log' / 'minimap_corners')
    return parser.parse_args()


def main():
    args = parse_args()
    from module.config.config import Config
    from module.device.device import Device
    from tasks.GameUi.game_ui import GameUi

    config = Config(args.config)
    device = Device(config)
    game = GameUi(config, device)
    print(f'设备: {device.serial}')
    game.map_pan_to_corner("bottom_left")
    # if not game.map_open_minimap_tap():
    #     raise SystemExit('小地图打不开（确认当前在主页面）')
    #
    # stamp = time.strftime('%Y%m%d_%H%M%S')
    # out_dir = args.save_dir / stamp
    # out_dir.mkdir(parents=True, exist_ok=True)
    # corners = (args.corner,) if args.corner else CORNERS
    #
    # failed = []
    # for corner in corners:
    #     started = time.time()
    #     ok = game.map_pan_to_corner(corner, max_drags=args.max_drags, settle=args.settle)
    #     cost = time.time() - started
    #     annotated, arrows = annotate(game, corner, ok)
    #     cv2.imwrite(str(out_dir / f'{corner}_raw.png'),
    #                 cv2.cvtColor(game.device.image, cv2.COLOR_RGB2BGR))
    #     cv2.imwrite(str(out_dir / f'{corner}.png'), annotated)
    #     print(f'{corner}: ok={ok} arrows={sorted(arrows)} cost={cost:.2f}s')
    #     if not ok:
    #         failed.append(corner)

    print(f'结果图目录: {out_dir}')
    print(f'完成: {len(corners) - len(failed)}/{len(corners)}，失败: {failed or "无"}')
    print('小地图保持打开（停在最后一个角落）')


if __name__ == '__main__':
    main()
