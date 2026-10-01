# This Python file uses the following encoding: utf-8
"""临时：测试背包仙玉碎片数量识别（不消耗仙石）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from tasks.XianfuJiuchongtian.script_task import ScriptTask

c = Config('oas1')
d = Device(c)
t = ScriptTask(c, d)

print('=== 南极 ===')
print(t.bag_piece_counts('南极'))
print('=== 北极 ===')
print(t.bag_piece_counts('北极'))
