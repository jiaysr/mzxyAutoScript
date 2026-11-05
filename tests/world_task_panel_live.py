# -*- coding: utf-8 -*-
"""
真机验证：世界任务「打开世界之巅活动弹窗 + 切到跨服任务列表」

走 ScriptTask 里的正式实现：find_npc(入口) -> open_activity_panel() -> switch_tab(跨服任务)
-> 确认「接取」按钮可见 -> 关掉弹窗。中途每个关键画面都存到 log/world_task/。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\world_task_panel_live.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2

from module.config.config import Config
from module.device.device import Device
from tasks.WorldTask.script_task import ScriptTask

OUT = PROJECT_ROOT / 'log' / 'world_task'


def save(image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    print(f'  saved {path}', flush=True)


def step(title: str) -> None:
    print(f'\n--- {title}', flush=True)


def main() -> bool:
    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)

    step('0. 清理现场（残留对话框 / 弹窗）')
    if task.npc_dialog() is not None:
        print('closing npc dialog:', task.close_npc_dialog(), flush=True)
    if task.activity_panel_open():
        print('closing activity panel:', task.close_activity_panel(), flush=True)

    step('1. 回主页面')
    task.prepare_game()

    step('2. 找入口 NPC')
    if not task.find_npc(task.NPC_NEW):
        print('find_npc(new) failed', flush=True)
        return False
    print('dialog =', task.npc_dialog(), flush=True)

    step('3. 点「查看世界之巅活动」打开弹窗')
    if not task.open_activity_panel():
        save(device.image, 'flow_00_panel_fail.png')
        print('open_activity_panel failed', flush=True)
        return False
    save(device.image, 'flow_01_panel.png')
    print('tabs =', task.tab_items(), flush=True)

    step('4. 切到「跨服任务」')
    if not task.switch_tab(task.ACTIVITY_TASK_TAB):
        print('switch_tab failed', flush=True)
        return False
    task.screenshot()
    save(device.image, 'flow_02_task_tab.png')
    accepted_visible = task.appear(task.I_ITEM_5)
    print('接取按钮可见 =', accepted_visible, flush=True)
    print('tabs =', task.tab_items(), flush=True)

    step('5. 反方向验证：先切到最右边的「荣誉商店」，再回世界之巅（必须往左翻）')
    far_ok = task.switch_tab('荣誉商店')
    task.screenshot()
    save(device.image, 'flow_03_shop_tab.png')
    print('switch_tab(荣誉商店) =', far_ok, 'tabs =', task.tab_items(), flush=True)

    home_ok = task.ensure_activity_tab(task.ACTIVITY_HOME_TAB)
    task.screenshot()
    save(device.image, 'flow_04_home_tab.png')
    print('ensure_activity_tab(世界之巅) =', home_ok,
          '黄帝立绘 =', task.appear(task.I_ITEM_4),
          'tabs =', task.tab_items(), flush=True)

    step('6. 再切回跨服任务（确认反向翻页之后还能正常用）')
    back_ok = task.switch_tab(task.ACTIVITY_TASK_TAB)
    task.screenshot()
    save(device.image, 'flow_05_back_task.png')
    print('切回跨服任务 =', back_ok, '接取可见 =', task.appear(task.I_ITEM_5),
          'tabs =', task.tab_items(), flush=True)

    step('7. 收尾：关掉弹窗')
    closed = task.close_activity_panel()
    print('close_activity_panel =', closed,
          '面板还开着?', task.activity_panel_open(), flush=True)

    ok = accepted_visible and far_ok and home_ok and back_ok and closed
    print(f'\n结果: {"PASS" if ok else "FAIL"}', flush=True)
    return ok


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
