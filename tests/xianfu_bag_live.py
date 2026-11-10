# This Python file uses the following encoding: utf-8
"""
临时真机脚本：测试背包仙玉碎片数量识别（不消耗仙石）

**这是真机脚本，不是单测**：会在模块里建 Device，连模拟器、连不上还会尝试启动模拟器。
所以文件名必须是 `*_live.py`，并且代码放进 `__main__` 里 —— 名字一旦落到 pytest 的
收集模式（`test_*.py` / `*_test.py`），跑一次全量单测就会把模拟器反复拉起来
（2026-11-05 的教训：3 次 × 120s 启动重试，还在正在运行的模拟器里滑了一轮背包）。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\xianfu_bag_live.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from module.config.config import Config
    from module.device.device import Device
    from tasks.XianfuJiuchongtian.script_task import ScriptTask

    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)

    print('=== 南极 ===')
    print(task.bag_piece_counts('南极'))
    print('=== 北极 ===')
    print(task.bag_piece_counts('北极'))


if __name__ == '__main__':
    main()
