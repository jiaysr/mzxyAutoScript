# -*- coding: utf-8 -*-
"""
pytest 收集保护：别把真机脚本当单测 import

`tests/` 里有两类文件：
- 单测：不连设备，随时可以全量跑（`test_*.py`）；
- 真机脚本：模块顶层就建 `Config` / `Device`，会连模拟器、连不上还会**尝试启动模拟器**
  并在游戏里点/滑。它们必须命名 `*_live.py`，代码放在 `if __name__ == '__main__'` 里。

教训（2026-11-05）：`tests/xianfu_bag_test.py` 名字命中 pytest 默认收集模式
（`test_*.py` / `*_test.py`），顶层直接 `Config('oas1')` + `Device(c)`，于是「跑一遍全量单测」
= 启动模拟器 3 次 × 120s 重试 + 在正在运行的模拟器里滑背包。

这里兜住老命名，避免再被收进来；新脚本请直接用 `*_live.py`。
"""
collect_ignore_glob = [
    # 真机脚本（不该在收集阶段被 import）
    '*_live.py',
]
