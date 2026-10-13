# This Python file uses the following encoding: utf-8
"""
背包修理装备测试：python tests/test_bag_repair.py [次数]
默认跑 2 次：第一次走确认修理流程，第二次应命中「无需修理」提示。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from tasks.GameUi.game_ui import GameUi


def main():
    times = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    config = Config('oas1')
    device = Device(config)
    game = GameUi(config, device)
    for i in range(1, times + 1):
        logger.hr(f'Bag repair test round {i}/{times}')
        ok = game.bag_repair_equipment()
        logger.info(f'REPAIR round {i} -> {ok}')


if __name__ == '__main__':
    main()
