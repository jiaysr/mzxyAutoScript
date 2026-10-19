# This Python file uses the following encoding: utf-8
"""实时模板匹配调试：在模拟器画面指定 ROI 内匹配模板并实时标注。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\template_match_debug.py

默认模板为同目录 template_match_debug_template.png（从
Screenshot_20261019-112101.png 的 453,57,36,34 裁出），
默认搜索区域 440,42,66,63。

toolkit 内的 OpenCV 是 headless 版本，没有 cv2.imshow，
所以标注画面通过内置 HTTP 服务以 MJPEG 流输出，浏览器会自动打开。
Ctrl+C 或网页上的「停止」按钮退出。
"""
import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(PROJECT_ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.chdir(PROJECT_ROOT)

from live_mjpeg_viewer import FrameViewer

DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "template_match_debug_template.png"
DEFAULT_SEARCH = (440, 42, 66, 63)
DEFAULT_PORT = 22268

def parse_roi(value: str) -> tuple:
    try:
        x, y, w, h = (int(v) for v in value.replace(" ", "").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("ROI 格式应为 x,y,w,h")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("ROI 的宽高必须为正数")
    return x, y, w, h


def load_template(path: Path) -> np.ndarray:
    template = cv2.imread(str(path))
    if template is None:
        raise FileNotFoundError(f"模板读取失败: {path}")
    return template


def match_in_roi(frame_bgr: np.ndarray, template: np.ndarray, roi: tuple):
    sx, sy, sw, sh = roi
    th, tw = template.shape[:2]
    if th > sh or tw > sw:
        raise ValueError(
            f"模板 {tw}x{th} 大于搜索区域 {sw}x{sh}，无法匹配"
        )
    source = frame_bgr[sy:sy + sh, sx:sx + sw]
    result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), (sx + max_loc[0], sy + max_loc[1])


def annotate(frame_bgr: np.ndarray, roi: tuple, match_pos: tuple,
             template_shape: tuple, score: float, hit: bool, threshold: float) -> np.ndarray:
    view = frame_bgr.copy()
    sx, sy, sw, sh = roi
    cv2.rectangle(view, (sx, sy), (sx + sw - 1, sy + sh - 1), (0, 255, 255), 1)

    mx, my = match_pos
    th, tw = template_shape[:2]
    color = (0, 200, 0) if hit else (0, 0, 255)
    cv2.rectangle(view, (mx, my), (mx + tw - 1, my + th - 1), color, 2)
    cx, cy = mx + tw // 2, my + th // 2
    cv2.drawMarker(view, (cx, cy), color, cv2.MARKER_CROSS, 8, 1)

    label = f"{'HIT' if hit else 'MISS'}  score={score:.3f}  thr={threshold:.2f}"
    cv2.putText(view, label, (sx, sy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(view, label, (sx, sy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return view


def build_display(frame_bgr: np.ndarray, roi: tuple, match_pos: tuple, template_shape: tuple,
                  score: float, hit: bool, threshold: float, fps: float, zoom: int) -> np.ndarray:
    view = annotate(frame_bgr, roi, match_pos, template_shape, score, hit, threshold)

    sx, sy, sw, sh = roi
    pad = 12
    x0, y0 = max(0, sx - pad), max(0, sy - pad)
    x1, y1 = min(view.shape[1], sx + sw + pad), min(view.shape[0], sy + sh + pad)
    crop = view[y0:y1, x0:x1]
    zoom = max(1, min(zoom, (view.shape[0] - 90) // crop.shape[0]))
    zoomed = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)

    panel_width = max(zoomed.shape[1] + 20, 220)
    panel = np.full((view.shape[0], panel_width, 3), 32, np.uint8)
    cv2.putText(panel, "ROI ZOOM", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
    status = "HIT" if hit else "MISS"
    status_color = (0, 200, 0) if hit else (0, 0, 255)
    cv2.putText(panel, status, (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2, cv2.LINE_AA)
    cv2.putText(panel, f"score {score:.4f}", (10, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.putText(panel, f"fps {fps:.1f}", (10, 96), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
    panel[110:110 + zoomed.shape[0], 10:10 + zoomed.shape[1]] = zoomed

    return np.hstack([view, panel])


def parse_args():
    parser = argparse.ArgumentParser(description="实时模板匹配并标注")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 中的配置名")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="模板图片路径")
    parser.add_argument("--search", type=parse_roi, default=DEFAULT_SEARCH, help="搜索区域 x,y,w,h")
    parser.add_argument("--threshold", type=float, default=0.8, help="匹配阈值")
    parser.add_argument("--interval", type=float, default=0.2, help="截图间隔（秒）")
    parser.add_argument("--zoom", type=int, default=4, help="ROI 放大倍数")
    parser.add_argument("--frames", type=int, default=0, help="处理帧数，0 表示一直运行")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="浏览器查看端口，0 表示随机")
    parser.add_argument("--no-viewer", action="store_true", help="不启动浏览器查看器（只打印状态）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--save", type=Path, default=None, help="退出时保存最后一帧标注图")
    return parser.parse_args()


def main():
    args = parse_args()

    from module.device.screenshot import Screenshot

    template = load_template(args.template)
    template_shape = template.shape
    template_size = (template.shape[1], template.shape[0])

    print(f"模板: {args.template} ({template_size[0]}x{template_size[1]})")
    print(f"搜索区域: {args.search}, 阈值: {args.threshold}, 配置: {args.config}")

    device = Screenshot(config=args.config)
    print(f"设备: {device.serial}, 包名: {device.package}")

    viewer = None
    if not args.no_viewer:
        try:
            viewer = FrameViewer(
                port=args.port,
                title="OAS 模板匹配调试",
                subtitle="（黄框=搜索区域，绿/红框=命中/未命中）",
            )
        except OSError:
            viewer = FrameViewer(port=0, title="OAS 模板匹配调试")
        url = viewer.start()
        print(f"实时标注页面: {url}")
        if not args.no_browser:
            webbrowser.open(url)

    last_hit = None
    last_report = 0.0
    fps = 0.0
    frames = 0
    display = None

    try:
        while True:
            if viewer is not None and viewer.stop_event.is_set():
                print("收到停止请求，退出")
                break

            frame_start = time.time()
            try:
                image = device.screenshot()  # RGB
                frame = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            except Exception as exc:  # noqa: BLE001
                print(f"截图失败: {exc}")
                time.sleep(1)
                continue

            score, match_pos = match_in_roi(frame, template, args.search)
            hit = score >= args.threshold
            frames += 1

            now = time.time()
            elapsed = now - frame_start
            fps = 1 / elapsed if elapsed > 0 else 0.0

            if hit != last_hit:
                print(f"{'HIT ' if hit else 'MISS'} score={score:.4f} at={match_pos} "
                      f"template={template_size[0]}x{template_size[1]}")
                last_hit = hit
                last_report = now
            elif now - last_report >= 5:
                print(f"score={score:.4f} {'HIT' if hit else 'MISS'} at={match_pos}")
                last_report = now

            display = build_display(frame, args.search, match_pos, template_shape, score, hit,
                                    args.threshold, fps, args.zoom)

            if viewer is not None:
                ok, buffer = cv2.imencode(".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok:
                    viewer.update(buffer.tobytes())

            if args.frames and frames >= args.frames:
                break

            remain = args.interval - (time.time() - frame_start)
            if remain > 0:
                time.sleep(remain)
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        if viewer is not None:
            viewer.stop()
        if args.save and display is not None:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.save), display)
            print(f"标注图已保存: {args.save}")


if __name__ == "__main__":
    main()
