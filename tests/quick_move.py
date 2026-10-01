# This Python file uses the following encoding: utf-8
"""
临时快速移动脚本：直接走到目标坐标（不做回主页面等额外检查）。
用于限时副本（如仙府九重天）里快速抵达怪物坐标。

用法：
    $env:PYTHONPATH="D:\project\mzxy\mzxyAutoScript"
    toolkit\python.exe tests\quick_move.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from tasks.GameUi.game_ui import GameUi

# 目标坐标（怪物）；命令行可覆盖：python tests/quick_move.py 210 158
TARGET = (210, 158)
TIMEOUT = 70


def main():
    target = TARGET
    if len(sys.argv) >= 3:
        target = (int(sys.argv[1]), int(sys.argv[2]))
    config = Config('oas1')
    device = Device(config)
    game = GameUi(config, device)

    start = game.map_move_read_pos()
    logger.info(f'QUICK start at {start}, target {target}')
    ok = game.map_move_to(target[0], target[1], ensure_main=False,
                          calibrate=False, timeout=TIMEOUT)
    end = game.map_move_read_pos()
    logger.info(f'QUICK move {target} -> {ok}, now at {end}')


if __name__ == '__main__':
    main()
