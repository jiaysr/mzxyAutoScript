# -*- coding: utf-8 -*-
"""
真机验证：世界之巅地图里做「木料之争」

走 ScriptTask 的正式实现：任务页点寻路 -> 关任务页 -> 等走到采集点 -> 循环采集
（为了不占太久，采集只给 90 秒；正式流程是 600 秒）。

用法（项目根目录执行）：
    toolkit\\python.exe tests\\world_task_wood_live.py
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
REPORT = OUT / 'wood_live_report.txt'
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
    task.task_monitor.start()

    try:
        log(f'地点 = {task.map_current_location()}')
        if not task.map_in_location(task.WORLD_PEAK_LOCATION):
            log('不在世界之巅，先切回主页面')
            task.prepare_game()
            log(f'进图 = {task.enter_world_peak()}')

        start = time.perf_counter()
        ok_page = task.open_task_page()
        log(f'打开任务页 = {ok_page} ({time.perf_counter() - start:.1f}s)')
        if not ok_page:
            REPORT.write_text('\n'.join(lines), encoding='utf-8')
            return False
        save(device.image, 'wood_00_task_page.png')

        rows = task.task_page_rows()
        log(f'任务页 {len(rows)} 行：')
        for name, status, wx, wy in rows:
            log(f'    "{name}" [{status}] 寻路@({wx},{wy})')

        start = time.perf_counter()
        walked = task.task_page_walk_to(task.WOOD_TASK)
        log(f'点「{task.WOOD_TASK}」寻路 = {walked} ({time.perf_counter() - start:.1f}s)')
        task.close_task_page()
        save(device.image, 'wood_01_after_walk_click.png')

        start = time.perf_counter()
        arrived = task.map_wait_arrive_at(task.WOOD_TARGET_POS, timeout=90)
        log(f'走到 {task.WOOD_TARGET_POS} = {arrived} '
            f'({time.perf_counter() - start:.1f}s) 当前 {task.map_move_read_pos()}')
        save(device.image, 'wood_02_arrived.png')

        start = time.perf_counter()
        done = task.gather_until_complete(task.WOOD_TASK, timeout=90)
        log(f'采集（90s 上限）= {done} ({time.perf_counter() - start:.1f}s)')
        log(f'监测到的完成提示：{task.completed_tasks}')
        log(f'动作按钮当前 = {task.ui_target_action()}，锁定 = {task.ui_target_locked()}')
        save(device.image, 'wood_03_after_gather.png')

        ok = ok_page and walked and arrived
        log(f'\n结果: {"PASS" if ok else "FAIL"}'
            f'（采集{"完成" if done else "未完成，流程本身没问题"}）')
        return ok
    except TaskEnd as e:
        log(f'被中断：{e}')
        return False
    finally:
        task.task_monitor.stop()
        REPORT.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
