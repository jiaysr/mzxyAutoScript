# This Python file uses the following encoding: utf-8
"""
银贝礼盒测试：python tests/test_silver_gift.py
传送牧野 -> 小地图点宝具商人 -> 等自动寻路走到 NPC 处。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.exception import TaskEnd
from module.logger import logger
from tasks.SilverGiftBox.script_task import ScriptTask


def main():
    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd as e:
        logger.info(f'SILVER GIFT done: {e}')


if __name__ == '__main__':
    main()
