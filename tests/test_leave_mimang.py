# This Python file uses the following encoding: utf-8
"""临时：验证 leave_mimang（进迷窟 -> 离开回沼泽）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from tasks.YuwaiMimang.script_task import ScriptTask


def main():
    config = Config('oas1')
    device = Device(config)
    t = ScriptTask(config, device)
    cfg = config.model.yuwai_mimang.yuwai_mimang_config

    t.close_leftover_dialog()
    t.map_close_minimap()
    loc = t.map_move_read_pos()
    print('起点:', loc)
    if loc is None:
        print('读不到坐标')
        return

    if not t.map_name_match(loc[0], t.MAP_NAME):
        print('不在迷窟，先进图...')
        if not t.enter_mimang(cfg):
            print('入图失败')
            return
        print('入图后:', t.map_move_read_pos())

    print('=== 调用 leave_mimang ===')
    ok = t.leave_mimang(cfg)
    print('leave_mimang ->', ok)
    print('最终位置:', t.map_move_read_pos())

    # 再跑一次 enter/leave 循环，确认可重复
    print('=== 第二次：再进图 -> 再离开 ===')
    if t.enter_mimang(cfg):
        print('第二次入图:', t.map_move_read_pos())
        ok2 = t.leave_mimang(cfg)
        print('第二次 leave ->', ok2)
        print('最终位置:', t.map_move_read_pos())
    else:
        print('第二次入图失败')


if __name__ == '__main__':
    main()