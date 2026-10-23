# This Python file uses the following encoding: utf-8
"""7 个技能位冷却状态实时监测：浏览器里看带标注的实时画面。

用法（项目根目录执行，游戏切到能看到技能栏的画面/战斗中）：
    toolkit\\python.exe tests\\skill_cooldown_monitor.py
    toolkit\\python.exe tests\\skill_cooldown_monitor.py --interval 0.1 --port 22270

画面说明：
- 每个技能位画一个框：绿色 = 冷却好，红色 = 冷却中，框内左上角是技能序号
- 右侧面板逐个列出 技能序号 / 状态 / 当前分 / 基准分 / 比例
- 页面地址 http://127.0.0.1:22270/ （自动打开浏览器），点「停止」或 Ctrl+C 退出

判定逻辑和任务里完全一致（tasks/GameUi/navigation/battle_skill.py）：
分数 = 按钮中心圆盘的「中位灰度 x 中位彩度 / 255」，
当前分 < 基准分 x ratio 判为冷却中；基准分默认读
tasks/GameUi/res/battle/res_skill<1-7>_ready.png（「冷却好」的截图素材）。
加 --auto-ref 时基准分会按运行中观测到的最高分自动抬高（缺素材时兜底）。

参数：
    --list  只打印 7 个技能位的区域和基准分，然后退出（不连设备）
"""
import argparse
import os
import sys
import time
import webbrowser
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

DEFAULT_RATIO = 0.6
DEFAULT_FLOOR = 0.0
DEFAULT_INTERVAL = 0.2
DEFAULT_PORT = 22270
REPORT_INTERVAL = 1.0
PANEL_WIDTH = 430

STATE_READY = "ready"
STATE_COOLING = "cooling"
STATE_UNKNOWN = "unknown"

COLOR_READY = (70, 210, 70)
COLOR_COOLING = (240, 70, 60)
COLOR_UNKNOWN = (200, 200, 90)
COLOR_TEXT = (225, 225, 225)
COLOR_DIM = (165, 165, 165)

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)
_FONT_CACHE = {}


def cjk_font(size: int):
    """中文字体（PIL 默认字体不支持中文）"""
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


def skill_regions() -> dict:
    """7 个技能位的判定区域（来自战斗公共常量）"""
    from tasks.GameUi.navigation.battle import SimpleBattle
    return dict(SimpleBattle.BATTLE_SKILL_CHECK_REGIONS)


def load_references() -> dict:
    """从 GameUi 的「冷却好」基准素材算基准分（和任务运行时同一套素材）"""
    from tasks.GameUi.assets import GameUiAssets
    from tasks.GameUi.navigation.battle import SimpleBattle
    from tasks.GameUi.navigation.battle_skill import skill_region_score

    references = {}
    for index, attr_name in SimpleBattle.BATTLE_SKILL_READY_ASSET.items():
        rule = getattr(GameUiAssets, attr_name, None)
        if rule is None:
            continue
        try:
            image = rule.image  # RGB
            height, width = image.shape[:2]
            references[index] = skill_region_score(image, (0, 0, width, height))
        except Exception as exc:  # noqa: BLE001
            print(f"技能{index + 1} 基准素材读取失败（{attr_name}）: {exc}")
    return references


def evaluate_skills(frame_rgb: np.ndarray, regions: dict, references: dict,
                    ratio: float, floor: float, auto_ref: bool = False) -> list:
    """逐技能打分并判定状态，返回 [dict(index, score, ref, ratio, state)]"""
    from tasks.GameUi.navigation.battle_skill import skill_region_score

    rows = []
    for index in sorted(regions):
        score = skill_region_score(frame_rgb, regions[index])
        if auto_ref and score > references.get(index, 0.0):
            references[index] = score
        reference = references.get(index, 0.0)
        if reference <= 0:
            state = STATE_UNKNOWN
            current_ratio = 0.0
        else:
            current_ratio = score / reference
            state = STATE_COOLING if score < max(reference * ratio, floor) else STATE_READY
        rows.append({"index": index, "score": score, "ref": reference,
                     "ratio": current_ratio, "state": state})
    return rows


def state_color(state: str) -> tuple:
    if state == STATE_READY:
        return COLOR_READY
    if state == STATE_COOLING:
        return COLOR_COOLING
    return COLOR_UNKNOWN


def state_text(state: str) -> str:
    return {STATE_READY: "冷却好", STATE_COOLING: "冷却中", STATE_UNKNOWN: "无基准"}[state]


def build_display(frame_rgb: np.ndarray, regions: dict, rows: list, meta: dict) -> np.ndarray:
    """左：实时画面 + 7 个技能框；右：状态面板"""
    view = frame_rgb.copy()
    for row in rows:
        index = row["index"]
        x, y, w, h = regions[index]
        color = state_color(row["state"])
        cv2.rectangle(view, (x, y), (x + w - 1, y + h - 1), color, 2)
        # 序号小徽标（框内左上角），状态看框颜色/右侧面板
        cv2.rectangle(view, (x, y), (x + 17, y + 17), color, -1)
        cv2.putText(view, str(index + 1), (x + 3, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)

    panel = np.full((view.shape[0], PANEL_WIDTH, 3), 28, np.uint8)
    labels = [
        ("技能冷却实时监测", (view.shape[1] + 16, 16), COLOR_TEXT, 22),
        ("绿=冷却好  红=冷却中  黄=无基准", (view.shape[1] + 16, 50), COLOR_DIM, 15),
    ]
    y = 88
    for row in rows:
        color = state_color(row["state"])
        labels.append((f"技能{row['index'] + 1}  {state_text(row['state'])}",
                       (view.shape[1] + 16, y), color, 19))
        labels.append((f"分数 {row['score']:6.2f}   基准 {row['ref']:6.2f}   "
                       f"比例 {row['ratio']:.2f}",
                       (view.shape[1] + 16, y + 24), COLOR_DIM, 14))
        y += 54

    y += 8
    labels += [
        (f"fps {meta['fps']:.1f}   截图+判定 {meta['cost'] * 1000:.0f} ms   帧 {meta['frames']}",
         (view.shape[1] + 16, y), (160, 200, 255), 14),
        (f"ratio {meta['ratio']:.2f}   floor {meta['floor']:.2f}"
         + ("   自动基准 ON" if meta["auto_ref"] else ""),
         (view.shape[1] + 16, y + 22), COLOR_DIM, 14),
        (f"统计：{meta['ready']} 好 / {meta['cooling']} 冷却 / {meta['unknown']} 无基准",
         (view.shape[1] + 16, y + 44), COLOR_DIM, 14),
    ]
    return draw_labels(np.hstack([view, panel]), labels)


def capture_frame(device, method: str) -> np.ndarray:
    if method == "auto":
        return device.screenshot()
    if method == "emulator_extras":
        return device.screenshot_emulator_extras()
    if method == "droidcast_raw":
        return device.screenshot_droidcast_raw()
    return device.screenshot_adb()


def parse_args():
    parser = argparse.ArgumentParser(description="7 个技能位冷却状态实时监测（浏览器标注）")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 中的配置名")
    parser.add_argument("--method", choices=("auto", "emulator_extras", "droidcast_raw", "adb"),
                        default="auto", help="截图方式，auto=按配置")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="截图间隔（秒）")
    parser.add_argument("--ratio", type=float, default=DEFAULT_RATIO,
                        help="当前分 < 基准分 x ratio 判为冷却中")
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR,
                        help="冷却判定绝对下限分（0=不用，自动基准时兜底用）")
    parser.add_argument("--auto-ref", action="store_true",
                        help="基准分按运行中观测到的最高分自动抬高（缺素材时用）")
    parser.add_argument("--frames", type=int, default=0, help="处理帧数，0 表示一直运行")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="浏览器查看端口，0 表示随机")
    parser.add_argument("--no-viewer", action="store_true", help="不启动浏览器查看器（只打印结果）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--save", type=Path, default=None, help="退出时保存最后一帧标注图")
    parser.add_argument("--list", action="store_true", help="打印 7 个技能位区域和基准分后退出")
    return parser.parse_args()


def main():
    args = parse_args()

    regions = skill_regions()
    references = load_references()
    print(f"技能位 {len(regions)} 个：")
    for index in sorted(regions):
        ref = references.get(index, 0.0)
        print(f"  技能{index + 1}: 区域 {regions[index]}  基准分 {ref:.2f}")
    if args.list:
        return

    from module.device.screenshot import Screenshot

    device = Screenshot(config=args.config)
    print(f"设备: {device.serial}, 包名: {device.package}, 截图方式: {args.method}")
    print(f"判定: 当前分 < max(基准 x {args.ratio}, {args.floor}) -> 冷却中"
          f"{'（自动基准）' if args.auto_ref else ''}")

    viewer = None
    if not args.no_viewer:
        kwargs = dict(
            title="OAS 技能冷却监测",
            subtitle="（绿=冷却好，红=冷却中，框内数字=技能序号）",
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
    frames = 0
    fps = 0.0
    display = None
    last_report = 0.0
    last_states = {}
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
            for index, (x, y, w, h) in regions.items():
                if x + w > width or y + h > height:
                    raise SystemExit(f"技能{index + 1} 区域 {regions[index]} 超出画面 {width}x{height}")

            rows = evaluate_skills(frame, regions, references, args.ratio, args.floor,
                                   auto_ref=args.auto_ref)
            frames += 1
            cost = time.time() - frame_start
            fps = 1 / cost if cost > 0 else 0.0

            now = time.time()
            states = {row["index"]: row["state"] for row in rows}
            changed = {i: s for i, s in states.items()
                       if i in last_states and last_states[i] != s}
            last_states = states
            for index, state in changed.items():
                row = rows[index]
                print(f"技能{index + 1} -> {state_text(state)} "
                      f"(分数 {row['score']:.2f} / 基准 {row['ref']:.2f} = {row['ratio']:.2f})")
            if now - last_report >= REPORT_INTERVAL:
                last_report = now
                brief = " ".join(f"{row['index'] + 1}:{state_text(row['state'])}"
                                 f"({row['ratio']:.2f})" for row in rows)
                print(f"[{time.strftime('%H:%M:%S')}] {brief}")

            ready = sum(1 for row in rows if row["state"] == STATE_READY)
            cooling = sum(1 for row in rows if row["state"] == STATE_COOLING)
            display = build_display(frame, regions, rows, {
                "fps": fps, "cost": cost, "frames": frames,
                "ratio": args.ratio, "floor": args.floor, "auto_ref": args.auto_ref,
                "ready": ready, "cooling": cooling, "unknown": len(rows) - ready - cooling,
            })

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
