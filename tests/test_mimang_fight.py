# This Python file uses the following encoding: utf-8
"""
临时：域外迷窟打怪短测（默认 30s）。

进图（如已在图里就跳过）-> 走到基准点 -> 打怪 timeout 秒 -> 打印战果。
用 --timeout 改时长，用 --no-enter 跳过进图。

用法：
    $env:PYTHONPATH="D:\\project\\mzxy\\mzxyAutoScript"
    toolkit\\python.exe tests\\test_mimang_fight.py [--timeout 30] [--no-enter]
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.YuwaiMimang.script_task import ScriptTask

TIMEOUT = 30
ENTER = True

for i, arg in enumerate(sys.argv):
    if arg == '--timeout' and i + 1 < len(sys.argv):
        TIMEOUT = float(sys.argv[i + 1])
    if arg == '--no-enter':
        ENTER = False


def main():
    config = Config('oas1')
    device = Device(config)
    t = ScriptTask(config, device)
    cfg = config.model.yuwai_mimang.yuwai_mimang_config

    print(f'=== 0. 当前状态 (timeout={TIMEOUT}s, enter={ENTER}) ===')
    start = t.map_move_read_pos()
    print('location:', start)
    if start is None:
        print('读不到坐标，退出')
        return

    if ENTER and not t.map_name_match(start[0], t.MAP_NAME):
        print('=== 1. 不在迷窟，先入图 ===')
        if not t.enter_mimang(cfg):
            print('入图失败，退出')
            return
        print('入图后:', t.map_move_read_pos())
    else:
        print('=== 1. 已在迷窟，跳过入图 ===')

    print(f'=== 2. 打怪 {TIMEOUT}s（deadline 覆盖时段结束） ===')
    t0 = time.time()
    # 正式运行是打到时段结束（slot[1]，如 11:30）；这里用 deadline 模拟一个短窗口
    deadline = datetime.now() + timedelta(seconds=TIMEOUT)
    try:
        t.handle_window([], (datetime.now(), deadline), cfg, deadline=deadline)
        result = 'finished'
    except GameStuckError as e:
        result = f'GameStuckError: {e}'
    except TaskEnd as e:
        result = f'TaskEnd: {e}'
    elapsed = time.time() - t0
    print(f'=== 3. 结果: {result} (耗时 {elapsed:.1f}s) ===')
    print('最终位置:', t.map_move_read_pos())


if __name__ == '__main__':
    main()