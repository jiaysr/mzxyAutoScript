# -*- coding: utf-8 -*-
"""
真机验证：世界任务「接 7 个跨服任务 + 任务完成监测」

直接走 ScriptTask.world_task_flow()（正式实现）：
回主页面 -> 找守门人 -> 打开世界之巅活动弹窗 -> 跨服任务 tab -> 开始监测 -> 按顺序接任务
-> 打印接取结果 / 监测结果 -> 关掉弹窗。

任务完成提示是最顶层弹窗（任何界面都盖得住），只在左下角聊天行停留 1.5 秒，
所以点完每个「接取」都会连拍 2 秒。

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


def main() -> bool:
    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)
    cfg = config.world_task.world_task_config
    log(f'配置：地图={cfg.location} 加量={cfg.bonus_multiplier.value}')

    # 角色可能在挂机时被打死（域外灵岛不是安全区）：死亡由全局逻辑处理并抛 TaskEnd，
    # 复活后重试一次即可（真机 2026-11-05 17:35 就碰上过）
    for attempt in range(1, 3):
        try:
            task.prepare_game()
            ok = task.world_task_flow()
            break
        except TaskEnd as e:
            log(f'第 {attempt} 次尝试被中断：{e}')
            if attempt >= 2:
                log('连续被中断，放弃')
                REPORT.write_text('\n'.join(lines), encoding='utf-8')
                return False
            device.sleep(3)
    else:
        return False

    save(device.image, 'accept_live_final.png')

    log(f'\n流程结果：{ok}')
    log(f'本次接取：{task.accepted_tasks}')
    log(f'监测到的完成提示：{task.task_monitor.names}')
    log(f'去重后的已完成：{task.completed_tasks}')
    log(f'全部完成 = {task.all_tasks_completed()}')
    log(f'监测还在跑 = {task.task_monitor.enabled}')
    log(f'最近一次聊天栏 OCR：{task.task_monitor.last_text!r}')
    log(f'\n结果: {"PASS" if ok else "FAIL"}')

    REPORT.write_text('\n'.join(lines), encoding='utf-8')
    return ok


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
