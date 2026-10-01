# This Python file uses the following encoding: utf-8
"""
通用点按脚本：python tests/tap.py x y [次数] [间隔秒]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.logger import logger


def main():
    x, y = int(sys.argv[1]), int(sys.argv[2])
    times = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    interval = float(sys.argv[4]) if len(sys.argv) > 4 else 0.8
    hold = float(sys.argv[5]) if len(sys.argv) > 5 else 0
    config = Config('oas1')
    device = Device(config)
    for _ in range(times):
        if hold > 0:
            device.long_click(x=x, y=y, duration=hold, control_name='TAP')
        else:
            device.click(x=x, y=y, control_name='TAP')
        device.click_record_clear()
        device.sleep(interval)
    logger.info(f'TAP ({x},{y}) x{times} hold={hold} done')


if __name__ == '__main__':
    main()
