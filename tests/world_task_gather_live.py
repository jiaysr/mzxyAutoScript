# -*- coding: utf-8 -*-
"""
真机验证：世界之巅的三个采集类任务（木料之争 / 世界之巅 / 世界印记）

走 ScriptTask 的正式实现：任务页点寻路 -> 关任务页 -> 等走到完成点 -> 循环采集到完成。
每个任务的采集给 120 秒上限（正式流程 600 秒），已经完成的会自动跳过。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\world_task_gather_live.py
    toolkit\\python.exe tests\\world_task_gather_live.py 世界印记    # 只跑一个
"""
import sys
import time
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
REPORT = OUT / 'gather_live_report.txt'
PER_TASK_GATHER = 120
lines = []


def log(text: str) -> None:
    print(text, flush=True)
    lines.append(text)


def save(image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / name), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


def main() -> bool:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)
    task.task_monitor.start()

    ok = True
    try:
        log(f'地点 = {task.map_current_location()}')
        if not task.map_in_location(task.WORLD_PEAK_LOCATION):
            task.prepare_game()
            log(f'进图 = {task.enter_world_peak()}')

        for name, position in task.GATHER_TASKS:
            if only and name != only:
                continue
            log(f'\n=== {name}（完成点 {position}）')
            start = time.perf_counter()
            if not task.open_task_page():
                log('任务页打不开')
                ok = False
                break
            row = task.find_task_page_row(name)
            for item in task.task_page_rows():
                log(f'    "{item[0]}" [{item[1]}] 寻路@({item[2]},{item[3]})')
            if row is None:
                log(f'任务页里没有 [{name}]')
                task.close_task_page()
                ok = False
                break
            if '完成' in row[1]:
                log(f'[{name}] 状态已完成，跳过')
                task.close_task_page()
                continue
            walked = task.task_page_walk_to(name, row=row)
            # 点完寻路游戏会自己关掉任务页（没关才兜底）
            closed = task.wait_task_page_closed()
            log(f'点寻路 = {walked}，任务页自动关闭 = {closed}')

            start = time.perf_counter()
            arrived = task.map_wait_arrive_at(position, timeout=90)
            log(f'走到 {position} = {arrived} '
                f'({time.perf_counter() - start:.1f}s) 当前 {task.map_move_read_pos()}')
            save(device.image, f'gather_{name}_arrived.png')

            start = time.perf_counter()
            done = task.gather_until_complete(name, timeout=PER_TASK_GATHER)
            log(f'采集 = {done} ({time.perf_counter() - start:.1f}s)')
            save(device.image, f'gather_{name}_after.png')
            if not done:
                log(f'[{name}] 没采完（可能附近没刷新出采集物），先停')
                ok = False
                break

        log(f'\n监测到的完成提示：{task.completed_tasks}')
        log(f'\n结果: {"PASS" if ok else "FAIL"}')
        return ok
    except TaskEnd as e:
        log(f'被中断：{e}')
        return False
    finally:
        task.task_monitor.stop()
        REPORT.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
