# This Python file uses the following encoding: utf-8
"""任务完成提示实时监测：OCR 左下角聊天栏并实时标注。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\task_complete_monitor.py

监测 ROI 默认 0,679,638,37，先用固定前缀模板
`task_complete_trigger_template.png`（`[系]任务`）做第一级匹配，
命中后才对该行做 OCR，识别形如
`[系]任务：【仙盟】清点军备已完成` 的系统消息并提取任务名。
标注画面通过浏览器 MJPEG 查看器显示（toolkit 的 OpenCV 无 GUI）。
Ctrl+C 或网页上的「停止」按钮退出。
"""
import argparse
import json
import os
import re
import sys
import time
import webbrowser
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(PROJECT_ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.chdir(PROJECT_ROOT)

from live_mjpeg_viewer import FrameViewer

DEFAULT_ROI = (0, 679, 638, 37)
DEFAULT_PORT = 22269
DEFAULT_THRESHOLD = 0.8
DEFAULT_DEDUP = 5.0
DEFAULT_TRIGGER_TEMPLATE = Path(__file__).resolve().parent / "task_complete_trigger_template.png"
DEFAULT_TRIGGER_THRESHOLD = 0.9
HIGHLIGHT_SECONDS = 3.0
HISTORY_SIZE = 5

TASK_RE = re.compile(r"任务\s*[:：]\s*(?P<name>.+?)\s*已完成")
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)
_FONT_CACHE = {}


def parse_roi(value: str) -> tuple:
    try:
        x, y, w, h = (int(v) for v in value.replace(" ", "").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("ROI 格式应为 x,y,w,h")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("ROI 的宽高必须为正数")
    return x, y, w, h


def normalize_text(text: str) -> str:
    text = text.replace("：", ":").replace("\u3000", " ")
    return re.sub(r"\s+", "", text)


def extract_task_name(text: str) -> str | None:
    match = TASK_RE.search(normalize_text(text))
    if not match:
        return None
    name = match.group("name").strip(":;；,，.。")
    return name or None


def load_template(path: Path) -> np.ndarray:
    template = cv2.imread(str(path))
    if template is None:
        raise FileNotFoundError(f"触发模板读取失败: {path}")
    return cv2.cvtColor(template, cv2.COLOR_BGR2RGB)


def match_template(frame_rgb: np.ndarray, template_rgb: np.ndarray, roi: tuple):
    x, y, w, h = roi
    th, tw = template_rgb.shape[:2]
    if th > h or tw > w:
        raise ValueError(f"触发模板 {tw}x{th} 大于搜索区域 {w}x{h}")
    source = frame_rgb[y:y + h, x:x + w]
    result = cv2.matchTemplate(source, template_rgb, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), (x + max_loc[0], y + max_loc[1])


def cjk_font(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for path in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[size] = font
            return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def draw_labels(image_rgb: np.ndarray, labels: list) -> np.ndarray:
    """labels: [(text, (x, y), (r, g, b), size)]"""
    if not labels:
        return image_rgb
    pil = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil)
    for text, (x, y), color, size in labels:
        font = cjk_font(size)
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        draw.rectangle([left - 3, top - 2, right + 3, bottom + 2], fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=tuple(color))
    return np.array(pil)


def clip_text(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build_display(frame_rgb: np.ndarray, roi: tuple, ocr_text: str, task_name: str | None,
                  score: float, history: list, fps: float, cost: float,
                  highlighted: bool, trigger_score: float, triggered: bool,
                  trigger_pos: tuple, trigger_shape: tuple) -> np.ndarray:
    view = frame_rgb.copy()
    x, y, w, h = roi
    color = (0, 220, 0) if highlighted else (255, 200, 0)
    cv2.rectangle(view, (x, y), (x + w - 1, y + h - 1), color, 2)

    if triggered:
        tx, ty = trigger_pos
        th, tw = trigger_shape[:2]
        cv2.rectangle(view, (tx, ty), (tx + tw - 1, ty + th - 1), (80, 180, 255), 1)

    labels = []
    label_y = max(6, y - 28)
    if highlighted and task_name:
        labels.append((f"任务完成: {task_name}", (max(6, x), label_y), (120, 255, 120), 20))
    elif triggered:
        labels.append(("检测到任务系统消息，OCR 中...", (max(6, x), label_y), (120, 220, 255), 18))
    else:
        labels.append(("等待任务完成提示...", (max(6, x), label_y), (255, 220, 120), 18))

    panel_w = 520
    panel_x = view.shape[1] + 14
    panel = np.full((view.shape[0], panel_w, 3), 28, np.uint8)
    labels += [
        ("任务完成实时监测", (panel_x, 18), (240, 240, 240), 22),
        (f"状态: {'已识别' if highlighted else '等待中'}",
         (panel_x, 56), (120, 255, 120) if highlighted else (170, 170, 170), 18),
        (f"trigger {trigger_score:.3f}  {'HIT' if triggered else 'WAIT'}",
         (panel_x, 86), (120, 220, 255) if triggered else (160, 160, 160), 16),
        (f"OCR: {clip_text(ocr_text, 30)}", (panel_x, 112), (200, 200, 200), 16),
        (f"任务: {clip_text(task_name, 30) if task_name else '-'}",
         (panel_x, 140), (255, 255, 160), 18),
        (f"score {score:.3f}   cost {cost * 1000:.0f} ms   fps {fps:.1f}",
         (panel_x, 170), (160, 200, 255), 16),
        ("最近完成:", (panel_x, 206), (200, 200, 200), 16),
    ]
    for index, (ts, name, _s) in enumerate(history):
        labels.append((f"{ts}  {clip_text(name, 24)}",
                       (panel_x, 234 + index * 28), (200, 230, 200), 16))

    return draw_labels(np.hstack([view, panel]), labels)


def capture_frame(device, method: str) -> np.ndarray:
    if method == "droidcast_raw":
        return device.screenshot_droidcast_raw()
    if method == "emulator_extras":
        return device.screenshot_emulator_extras()
    if method == "adb":
        return device.screenshot_adb()
    return device.screenshot()


def parse_args():
    parser = argparse.ArgumentParser(description="任务完成提示实时 OCR 监测")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 中的配置名")
    parser.add_argument("--roi", type=parse_roi, default=DEFAULT_ROI, help="聊天栏识别区域 x,y,w,h")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="OCR 置信度阈值")
    parser.add_argument("--trigger-template", type=Path, default=DEFAULT_TRIGGER_TEMPLATE,
                        help="第一级模板匹配的触发模板")
    parser.add_argument("--trigger-threshold", type=float, default=DEFAULT_TRIGGER_THRESHOLD,
                        help="触发模板匹配阈值")
    parser.add_argument("--interval", type=float, default=0.1, help="截图间隔（秒）")
    parser.add_argument("--method", choices=("droidcast_raw", "emulator_extras", "adb", "auto"),
                        default="droidcast_raw", help="截图方式")
    parser.add_argument("--dedup", type=float, default=DEFAULT_DEDUP,
                        help="同名任务去重时间（秒）")
    parser.add_argument("--frames", type=int, default=0, help="处理帧数，0 表示一直运行")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="浏览器查看端口，0 表示随机")
    parser.add_argument("--no-viewer", action="store_true", help="不启动浏览器查看器（只打印结果）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--save", type=Path, default=None, help="退出时保存最后一帧标注图")
    return parser.parse_args()


def main():
    args = parse_args()

    from module.device.screenshot import Screenshot
    from module.ocr.models import get_local_ocr_model

    x, y, w, h = args.roi
    print(f"OCR 区域: {args.roi}, 阈值: {args.threshold}, 配置: {args.config}")

    trigger_template = load_template(args.trigger_template)
    print(f"触发模板: {args.trigger_template} "
          f"({trigger_template.shape[1]}x{trigger_template.shape[0]}), "
          f"阈值: {args.trigger_threshold}")

    device = Screenshot(config=args.config)
    print(f"设备: {device.serial}, 包名: {device.package}, 截图方式: {args.method}")

    ocr = get_local_ocr_model()
    print(f"OCR 模型: {ocr}")

    viewer = None
    if not args.no_viewer:
        kwargs = dict(
            title="OAS 任务完成监测",
            subtitle="（黄框=OCR 区域，绿框=识别到任务完成）",
        )
        try:
            viewer = FrameViewer(port=args.port, **kwargs)
        except OSError:
            viewer = FrameViewer(port=0, **kwargs)
        url = viewer.start()
        print(f"实时标注页面: {url}")
        if not args.no_browser:
            webbrowser.open(url)

    method = args.method
    last_name = None
    last_report_at = 0.0
    last_triggered = False
    highlight_name = None
    highlight_until = 0.0
    history = deque(maxlen=HISTORY_SIZE)
    frames = 0
    fps = 0.0
    display = None

    try:
        while True:
            if viewer is not None and viewer.stop_event.is_set():
                print("收到停止请求，退出")
                break

            frame_start = time.time()
            try:
                frame = capture_frame(device, method)
            except Exception as exc:  # noqa: BLE001
                if method != "adb":
                    print(f"{method} 截图失败: {exc}，回退到 adb")
                    method = "adb"
                    continue
                print(f"截图失败: {exc}")
                time.sleep(1)
                continue

            height, width = frame.shape[:2]
            if x + w > width or y + h > height:
                print(f"ROI {args.roi} 超出画面 {width}x{height}")
                return

            roi_image = frame[y:y + h, x:x + w]
            trigger_score, trigger_pos = match_template(frame, trigger_template, args.roi)
            triggered = trigger_score >= args.trigger_threshold
            ocr_text = ""
            score = 0.0
            task_name = None
            if triggered:
                try:
                    ocr_text, score = ocr.ocr_single_line(roi_image)
                    if score >= args.threshold:
                        task_name = extract_task_name(ocr_text)
                except Exception as exc:  # noqa: BLE001
                    print(f"OCR 失败: {exc}")

            if triggered != last_triggered:
                print(f"TRIGGER {'HIT' if triggered else 'CLEAR'} "
                      f"score={trigger_score:.3f} at={trigger_pos}")
                last_triggered = triggered

            now = time.time()
            if task_name and (task_name != last_name or now - last_report_at >= args.dedup):
                history.append((time.strftime("%H:%M:%S"), task_name, round(score, 3)))
                print(f"TASK_COMPLETE name={task_name} score={score:.3f} "
                      f"raw={json.dumps(ocr_text, ensure_ascii=True)}")
                last_name = task_name
                last_report_at = now
                highlight_name = task_name
                highlight_until = now + HIGHLIGHT_SECONDS

            frames += 1
            cost = time.time() - frame_start
            fps = 1 / cost if cost > 0 else 0.0
            highlighted = highlight_name is not None and now < highlight_until

            display = build_display(
                frame, args.roi, ocr_text,
                highlight_name if highlighted else None,
                score, list(history), fps, cost, highlighted,
                trigger_score, triggered, trigger_pos, trigger_template.shape,
            )

            if viewer is not None:
                ok, buffer = cv2.imencode(
                    ".jpg", cv2.cvtColor(display, cv2.COLOR_RGB2BGR),
                    [int(cv2.IMWRITE_JPEG_QUALITY), 85],
                )
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
            cv2.imwrite(str(args.save), cv2.cvtColor(display, cv2.COLOR_RGB2BGR))
            print(f"标注图已保存: {args.save}")


if __name__ == "__main__":
    main()
