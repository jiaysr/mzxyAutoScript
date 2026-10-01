# This Python file uses the following encoding: utf-8
"""
临时快速攻击脚本：对着当前锁定的怪物连点攻击键（用于限时副本，如仙府九重天）。
用法：
    $env:PYTHONPATH="D:\project\mzxy\mzxyAutoScript"
    toolkit\python.exe tests\quick_attack.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from tasks.AncientHunt.assets import AncientHuntAssets
from tasks.GameUi.game_ui import GameUi

TIMES = 25
INTERVAL = 0.6


def main():
    config = Config('oas1')
    device = Device(config)
    game = GameUi(config, device)

    x, y = AncientHuntAssets.C_ATTACK.coord()
    logger.info(f'QUICK attack at ({x},{y}) x{TIMES}')
    for _ in range(TIMES):
        device.click(x=x, y=y, control_name='QUICK_ATTACK')
        device.click_record_clear()
        device.sleep(INTERVAL)
    logger.info('QUICK attack done')


if __name__ == '__main__':
    main()
