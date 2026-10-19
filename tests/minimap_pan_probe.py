# This Python file uses the following encoding: utf-8
"""
小地图视口平移探针（不移动角色）。

前提：设备上小地图已经打开（主页面右上角入口点开）。
在地图区域内做一次滑动/受控拖动，测量地图内容实际位移、惯性、四边箭头
指示的变化，并检测绿色圆点。不会点击地图点位，不会触发寻路。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\minimap_pan_probe.py
    toolkit\\python.exe tests\\minimap_pan_probe.py --mode swipe
    toolkit\\python.exe tests\\minimap_pan_probe.py --mode hold --hold 0.4
    toolkit\\python.exe tests\\minimap_pan_probe.py --start 700,350 --end 500,540 --no-restore

输出：
    log/minimap_pan_probe/<时间戳>/before.png / after.png / restored.png
    控制台打印位移、箭头、绿点数量；默认结束后反向拖回原位。
"""
import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 1280x720 下小地图窗口的固定几何（真机帧核对）
MAP_VIEW = (160, 115, 785, 525)          # 地图内容区 x,y,w,h
ARROW_ROI = (130, 85, 830, 560)          # 含顶部/左侧箭头，排除右侧 NPC 列表
WORLD_MAP_TPL = ROOT / 'tasks/GameUi/res/map/res_map_minimap_world_map.png'
WORLD_MAP_ROI = (940, 100, 180, 80)      # 「世界地图」按钮，用于判断小地图是否开着

DEFAULT_START = (700, 350)
DEFAULT_END = (500, 540)                 # 向左下拖：视口向上右移动，露出上面/右边内容


def parse_point(value: str):
    try:
        x, y = (int(v) for v in value.replace(' ', '').split(','))
    except ValueError:
        raise argparse.ArgumentTypeError('坐标格式应为 x,y')
    return x, y


def capture(device, method: str) -> np.ndarray:
    if method == 'emulator_extras':
        return device.screenshot_emulator_extras()
    if method == 'droidcast_raw':
        return device.screenshot_droidcast_raw()
    if method == 'adb':
        return device.screenshot_adb()
    return device.screenshot()


def minimap_open_score(frame_rgb: np.ndarray, template_bgr: np.ndarray) -> float:
    x, y, w, h = WORLD_MAP_ROI
    source = cv2.cvtColor(frame_rgb[y:y + h, x:x + w], cv2.COLOR_RGB2BGR)
    result = cv2.matchTemplate(source, template_bgr, cv2.TM_CCOEFF_NORMED)
    return float(result.max())


def detect_green_dots(frame_rgb: np.ndarray):
    """地图内容区内的纯绿圆点：G 高、R/B 很低。返回 [(cx, cy, w, h, area)]"""
    r, g, b = cv2.split(frame_rgb)
    mask = ((g > 120) & (r < 48) & (b < 48)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    view_x, view_y, view_w, view_h = MAP_VIEW
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    dots = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        ratio = w / max(h, 1)
        cx, cy = int(centroids[i][0]), int(centroids[i][1])
        if not (view_x <= cx < view_x + view_w and view_y <= cy < view_y + view_h):
            continue
        if area < 120 or w > 80 or h > 80 or not (0.3 < ratio < 3.2):
            continue
        dots.append((cx, cy, int(w), int(h), int(area)))
    return dots


def detect_blue_arrows(frame_rgb: np.ndarray):
    """地图窗口内的蓝色块，按靠近哪条边归类为 top/right/bottom/left。"""
    hsv = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, (95, 120, 120), (125, 255, 255))
    ax, ay, aw, ah = ARROW_ROI
    kept = np.zeros_like(mask)
    kept[ay:ay + ah, ax:ax + aw] = mask[ay:ay + ah, ax:ax + aw]
    kept = cv2.morphologyEx(kept, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(kept)
    vx, vy, vw, vh = MAP_VIEW
    arrows = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area < 80 or w > 60 or h > 60:
            continue
        cx, cy = int(centroids[i][0]), int(centroids[i][1])
        distances = {
            'top': abs(cy - vy),
            'bottom': abs(cy - (vy + vh)),
            'left': abs(cx - vx),
            'right': abs(cx - (vx + vw)),
        }
        side = min(distances, key=distances.get)
        if distances[side] > 45:
            continue
        arrows.append((side, cx, cy, int(w), int(h), int(area)))
    return arrows


def estimate_shift(before_rgb: np.ndarray, after_rgb: np.ndarray):
    """ORB + 相似变换估计地图内容位移（after - before），返回 (dx, dy, inliers, total)。"""
    x, y, w, h = MAP_VIEW
    a = cv2.cvtColor(before_rgb[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(after_rgb[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY)
    orb = cv2.ORB_create(nfeatures=3000, fastThreshold=8)
    kp1, des1 = orb.detectAndCompute(a, None)
    kp2, des2 = orb.detectAndCompute(b, None)
    if des1 is None or des2 is None:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(matcher.match(des1, des2), key=lambda m: m.distance)[:500]
    if len(matches) < 8:
        return None
    pts1 = np.float32([kp1[m.queryIdx].pt for m in matches])
    pts2 = np.float32([kp2[m.trainIdx].pt for m in matches])
    matrix, inliers = cv2.estimateAffinePartial2D(
        pts1, pts2, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=3000,
    )
    if matrix is None or inliers is None:
        return None
    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])
    return dx, dy, int(inliers.sum()), len(matches)


def annotate(frame_rgb: np.ndarray, dots, arrows, title: str) -> np.ndarray:
    view = frame_rgb.copy()
    x, y, w, h = MAP_VIEW
    cv2.rectangle(view, (x, y), (x + w - 1, y + h - 1), (0, 255, 255), 2)
    for cx, cy, dw, dh, _area in dots:
        cv2.circle(view, (cx, cy), max(dw, dh) // 2 + 4, (0, 255, 0), 2)
    for side, cx, cy, aw, ah, _area in arrows:
        cv2.rectangle(view, (cx - aw // 2, cy - ah // 2),
                      (cx + aw // 2, cy + ah // 2), (255, 128, 0), 2)
        cv2.putText(view, side, (cx + 8, cy - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 200, 80), 2, cv2.LINE_AA)
    cv2.putText(view, title, (x + 8, y + 24), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return view


def do_drag(device, mode: str, start, end, hold: float):
    if mode == 'hold':
        device.hold_drag(start, end, hold=hold, control_name='minimap_pan_probe')
    else:
        device.swipe(start, end, duration=(0.15, 0.25), control_name='minimap_pan_probe')
    device.click_record_clear()


def summarize(frame_rgb, template_bgr):
    dots = detect_green_dots(frame_rgb)
    arrows = detect_blue_arrows(frame_rgb)
    score = minimap_open_score(frame_rgb, template_bgr)
    return dots, arrows, score


def parse_args():
    parser = argparse.ArgumentParser(description='小地图视口平移探针（不移动角色）')
    parser.add_argument('--config', default='oas1')
    parser.add_argument('--method', choices=('emulator_extras', 'droidcast_raw', 'adb', 'auto'),
                        default='emulator_extras')
    parser.add_argument('--mode', choices=('swipe', 'hold'), default='swipe',
                        help='swipe=快速滑动；hold=拖到底按住再抬（抑制惯性）')
    parser.add_argument('--hold', type=float, default=0.4, help='hold 模式终点按住秒数')
    parser.add_argument('--start', type=parse_point, default=DEFAULT_START)
    parser.add_argument('--end', type=parse_point, default=DEFAULT_END)
    parser.add_argument('--settle', type=float, default=1.0, help='松手后等待地图停稳秒数')
    parser.add_argument('--no-restore', action='store_true', help='不做反向拖动还原视口')
    parser.add_argument('--dry-run', action='store_true', help='只截图检测箭头/绿点，不拖动')
    parser.add_argument('--save-dir', type=Path, default=ROOT / 'log' / 'minimap_pan_probe')
    return parser.parse_args()


def main():
    args = parse_args()
    from module.config.config import Config
    from module.device.device import Device
    from module.logger import logger

    template = cv2.imread(str(WORLD_MAP_TPL))
    if template is None:
        raise FileNotFoundError(f'小地图模板读取失败: {WORLD_MAP_TPL}')

    x, y, w, h = MAP_VIEW
    for point in (args.start, args.end):
        if not (x <= point[0] < x + w and y <= point[1] < y + h):
            raise SystemExit(f'拖动点 {point} 不在小地图内容区内 {MAP_VIEW}')

    config = Config(args.config)
    device = Device(config)
    logger.info(f'设备: {device.serial}, 截图方式: {args.method}')
    logger.info(f'拖动: {args.start} -> {args.end}, mode={args.mode}, hold={args.hold}s, settle={args.settle}s')

    before = capture(device, args.method)
    score = minimap_open_score(before, template)
    logger.info(f'小地图打开置信度: {score:.3f}')
    if score < 0.8:
        raise SystemExit('小地图未打开：先手动打开小地图再运行本探针')

    before_dots, before_arrows, _ = summarize(before, template)
    logger.info(f'滑动前绿点: {len(before_dots)} 个 -> '
                f'{[(d[0], d[1]) for d in before_dots[:12]]}')
    logger.info(f'滑动前蓝色箭头: {before_arrows}')

    stamp = time.strftime('%Y%m%d_%H%M%S')
    out_dir = args.save_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / 'before_raw.png'), cv2.cvtColor(before, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(out_dir / 'before.png'),
                cv2.cvtColor(annotate(before, before_dots, before_arrows, 'before'), cv2.COLOR_RGB2BGR))
    if args.dry_run:
        logger.info(f'--dry-run: 不拖动，结果图目录: {out_dir}')
        return

    do_drag(device, args.mode, args.start, args.end, args.hold)
    device.sleep(args.settle)

    after = capture(device, args.method)
    after_score = minimap_open_score(after, template)
    after_dots, after_arrows, _ = summarize(after, template)
    cv2.imwrite(str(out_dir / 'after_raw.png'), cv2.cvtColor(after, cv2.COLOR_RGB2BGR))
    logger.info(f'滑动后小地图置信度: {after_score:.3f}')
    logger.info(f'滑动后绿点: {len(after_dots)} 个 -> {[(d[0], d[1]) for d in after_dots[:12]]}')
    logger.info(f'滑动后蓝色箭头: {after_arrows}')

    shift = estimate_shift(before, after)
    if shift is None:
        logger.warning('位移估计失败（特征匹配不足）')
    else:
        dx, dy, inliers, total = shift
        drag_dx = args.end[0] - args.start[0]
        drag_dy = args.end[1] - args.start[1]
        logger.info(f'地图内容位移(ORB): dx={dx:.1f}, dy={dy:.1f}, '
                    f'inliers={inliers}/{total}')
        logger.info(f'拖动向量: dx={drag_dx}, dy={drag_dy}, '
                    f'位移/拖动比: {abs(dx / drag_dx) if drag_dx else 0:.2f}x, '
                    f'{abs(dy / drag_dy) if drag_dy else 0:.2f}y')
        annotated = annotate(after, after_dots, after_arrows, 'after')
        cv2.arrowedLine(annotated, (x + w // 2, y + h // 2),
                        (int(x + w // 2 + dx), int(y + h // 2 + dy)),
                        (255, 255, 255), 3, tipLength=0.15)
        cv2.putText(annotated, f'dx={dx:.1f} dy={dy:.1f}', (x + 8, y + h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(out_dir / 'after.png'), cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR))
    if after_score < 0.8:
        logger.error('滑动后小地图消失：拖动可能被识别成了点击，已停止（请检查角色是否开始寻路）')
        return

    if not args.no_restore:
        logger.info('反向拖动还原视口')
        do_drag(device, args.mode, args.end, args.start, args.hold)
        device.sleep(args.settle)
        restored = capture(device, args.method)
        restored_score = minimap_open_score(restored, template)
        cv2.imwrite(str(out_dir / 'restored_raw.png'), cv2.cvtColor(restored, cv2.COLOR_RGB2BGR))
        residual = estimate_shift(before, restored)
        if residual is not None:
            rdx, rdy, rinliers, rtotal = residual
            logger.info(f'还原残差: dx={rdx:.1f}, dy={rdy:.1f}, inliers={rinliers}/{rtotal}')
        logger.info(f'还原后小地图置信度: {restored_score:.3f}')
        cv2.imwrite(str(out_dir / 'restored.png'),
                    cv2.cvtColor(restored, cv2.COLOR_RGB2BGR))

    logger.info(f'结果图目录: {out_dir}')


if __name__ == '__main__':
    main()
