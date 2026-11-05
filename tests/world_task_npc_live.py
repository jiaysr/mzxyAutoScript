# -*- coding: utf-8 -*-
"""
真机验证：世界任务 NPC 识别（走 ScriptTask 里的正式实现，不另写逻辑）

流程：关掉残留对话框 -> 回主页面 -> 判断地图（不在就传送）-> 小地图到右上角
      -> 点入口 NPC -> 模板确认 -> 关掉 -> 点交付 NPC -> 模板确认

把整屏截图和模板分数存到 log/world_task/，失败时方便回看。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\world_task_npc_live.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2

from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from tasks.WorldTask.script_task import ScriptTask

OUT = PROJECT_ROOT / 'log' / 'world_task'


def save(image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    print(f'  saved {path}', flush=True)


def scores(task, image) -> str:
    """两个对话框模板在整屏上的原始分数（确认区分度）"""
    out = []
    for npc in (task.NPC_NEW, task.NPC_ITEM_2):
        rule = task.npc_template(npc)
        x, y, w, h = rule.roi_back
        source = image[y:y + h, x:x + w]
        res = cv2.matchTemplate(source, rule.image, cv2.TM_CCOEFF_NORMED)
        out.append(f'{npc}={cv2.minMaxLoc(res)[1]:.3f}')
    return ' '.join(out)


def step(title: str) -> None:
    print(f'\n--- {title}', flush=True)


def main() -> bool:
    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)

    step('0. 关掉残留对话框')
    print('dialog =', task.npc_dialog(), flush=True)
    if task.npc_dialog() is not None:
        print('closed =', task.close_npc_dialog(), flush=True)
        save(device.image, 'npc_30_after_close.png')

    step('1. 回主页面')
    task.prepare_game()
    save(device.image, 'npc_31_main.png')

    step('2. 判断地图')
    at_location = task.ensure_location()
    print('ensure_location =', at_location, flush=True)
    print('location =', task.map_current_location(), flush=True)
    save(device.image, 'npc_32_location.png')
    if not at_location:
        return False

    step('3. 找入口 NPC（世界之巅守门人）')
    ok_new = task.find_npc(task.NPC_NEW)
    print('find_npc(new) =', ok_new, 'dialog =', task.npc_dialog(), flush=True)
    print('scores:', scores(task, device.image), flush=True)
    save(device.image, 'npc_33_dialog_new.png')

    step('4. 关对话框')
    closed = task.close_npc_dialog()
    print('close_npc_dialog =', closed, 'dialog =', task.npc_dialog(), flush=True)
    save(device.image, 'npc_34_closed.png')

    step('5. 找交付 NPC（世界之巅任务榜）')
    ok_item = task.find_npc(task.NPC_ITEM_2)
    print('find_npc(item_2) =', ok_item, 'dialog =', task.npc_dialog(), flush=True)
    print('scores:', scores(task, device.image), flush=True)
    save(device.image, 'npc_35_dialog_item.png')

    step('6. 收尾：关掉对话框，别把游戏留在弹窗里')
    print('close_npc_dialog =', task.close_npc_dialog(), flush=True)

    print(f'\n结果: new={ok_new} item_2={ok_item} '
          f'{"PASS" if ok_new and ok_item else "FAIL"}', flush=True)
    return ok_new and ok_item


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
