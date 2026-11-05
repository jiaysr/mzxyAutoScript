# -*- coding: utf-8 -*-
"""
真机验证：世界任务「接 7 个跨服任务」（走 ScriptTask 里的正式实现）

流程：回主页面 -> 找守门人 -> 打开世界之巅活动弹窗 -> 跨服任务 tab -> 按顺序接任务
      -> 打印接取结果 + 任务完成提示监测结果 -> 关掉弹窗

任务完成监测在 ScriptTask.__init__ 里就绪，接任务全程每次截图都会过一遍，
所以「接取时当场判定完成」的任务也会记下来。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\world_task_accept_live.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2

from module.config.config import Config
from module.device.device import Device
from module.exception import TaskEnd
from tasks.WorldTask.script_task import ScriptTask

OUT = PROJECT_ROOT / 'log' / 'world_task'
REPORT = OUT / 'accept_live_report.txt'
lines = []


def log(text: str) -> None:
    print(text, flush=True)
    lines.append(text)


def save(image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / name), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


def step(title: str) -> None:
    log(f'\n--- {title}')


def dump_rows(task, tag: str) -> None:
    rows = task.task_rows()
    log(f'[{tag}] {len(rows)} 行')
    for text, x, y in rows:
        log(f'    ({x},{y}) "{text}"')


def main() -> bool:
    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)
    cfg = config.world_task.world_task_config
    log(f'配置：地图={cfg.location} 加量={cfg.use_bonus}')

    # 角色可能在挂机时被打死（域外灵岛不是安全区）：死亡由全局逻辑处理并抛 TaskEnd，
    # 复活后重试一次即可（真机 2026-11-05 17:35 就碰上过）
    for attempt in range(1, 3):
        try:
            return run_flow(task, device)
        except TaskEnd as e:
            log(f'第 {attempt} 次尝试被中断：{e}')
            if attempt >= 2:
                log('连续被中断，放弃')
                REPORT.write_text('\n'.join(lines), encoding='utf-8')
                return False
            device.sleep(3)
    return False


def run_flow(task: ScriptTask, device) -> bool:
    step('0. 准备：回主页面、清理残留弹窗')
    task.prepare_game()
    if task.npc_dialog() is not None:
        task.close_npc_dialog()
    if task.activity_panel_open():
        task.close_activity_panel()

    step('1. 找入口 NPC')
    if not task.find_npc(task.NPC_NEW):
        log('find_npc 失败')
        REPORT.write_text('\n'.join(lines), encoding='utf-8')
        return False

    step('2. 打开活动弹窗并切到跨服任务')
    if not task.open_activity_panel():
        log('open_activity_panel 失败')
        REPORT.write_text('\n'.join(lines), encoding='utf-8')
        return False
    if not task.switch_tab(task.ACTIVITY_TASK_TAB):
        log('switch_tab 失败')
        REPORT.write_text('\n'.join(lines), encoding='utf-8')
        return False
    dump_rows(task, '接取前')
    save(device.image, 'accept_live_00_before.png')

    step('3. 接任务')
    accepted = task.accept_tasks()
    save(device.image, 'accept_live_01_after.png')
    log(f'接取结果：{accepted}')
    dump_rows(task, '接取后')

    step('4. 任务完成提示监测')
    log(f'监测到的任务名：{task.task_monitor.names}')
    log(f'当场判定已完成：{task.completed_tasks}')
    log(f'最近一次聊天栏 OCR：{task.task_monitor.last_text!r}')

    step('5. 收尾：关掉弹窗')
    log(f'关弹窗 = {task.close_activity_panel()}')

    ok = len(accepted) > 0
    log(f'\n结果: {"PASS" if ok else "FAIL"}（接取 {len(accepted)} 个）')
    REPORT.write_text('\n'.join(lines), encoding='utf-8')
    return ok


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
