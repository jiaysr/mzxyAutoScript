# This Python file uses the following encoding: utf-8
"""EmulatorExtras 截图探针：验证雷电/MuMu 宿主侧截图后端。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\emulator_extras_probe.py
    toolkit\\python.exe tests\\emulator_extras_probe.py --compare

默认按 config/oas1.json 的 emulatorinfo 自动选择 LDPlayer/MuMu 后端；
LD 使用 ldopengl64.dll，MuMu 使用 external_renderer_ipc.dll。
"""
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def parse_args():
    parser = argparse.ArgumentParser(description="EmulatorExtras 截图探针")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 中的配置名")
    parser.add_argument("--frames", type=int, default=20, help="截图次数")
    parser.add_argument("--compare", action="store_true", help="同时测 DroidCast_raw 作为对比")
    parser.add_argument("--save", type=Path, default=Path("log/temp/emulator_extras_probe.png"),
                        help="保存最后一帧的路径")
    return parser.parse_args()


def bench(capture, frames: int):
    latencies = []
    image = None
    for _ in range(frames):
        start = time.time()
        image = capture()
        latencies.append(time.time() - start)
    latencies.sort()
    mean = statistics.mean(latencies)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95) - 1)]
    return image, mean, p95


def main():
    args = parse_args()

    from module.device.screenshot import Screenshot

    device = Screenshot(config=args.config)
    print(f"设备: {device.serial}, 包名: {device.package}")
    print(f"emulatorinfo: {device.config.script.device.emulatorinfo_type} "
          f"{device.config.script.device.emulatorinfo_name} "
          f"{device.config.script.device.emulatorinfo_path}")
    print(f"EmulatorExtras 支持: {device.emulator_extras_supported}")

    image, mean, p95 = bench(device.screenshot_emulator_extras, args.frames)
    print(f"EmulatorExtras: {image.shape} mean={mean*1000:.2f}ms p95={p95*1000:.2f}ms "
          f"fps={1/mean:.1f}")
    args.save.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.save), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    print(f"已保存: {args.save}")

    if args.compare:
        _, mean2, p95_2 = bench(device.screenshot_droidcast_raw, args.frames)
        print(f"DroidCast_raw: mean={mean2*1000:.2f}ms p95={p95_2*1000:.2f}ms fps={1/mean2:.1f}")
        print(f"加速比: {mean2/mean:.1f}x")

    device.release_emulator_extras()


if __name__ == "__main__":
    main()
