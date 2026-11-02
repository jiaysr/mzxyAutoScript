# This Python file uses the following encoding: utf-8
"""
临时：真机验证「背包寻答题券」（模板匹配优先 + OCR 兜底）

不消耗答题次数：找到答题券、点开详情面板确认是它，然后关掉面板，
**不点「立即使用」**。

用法（游戏停在主页面）：
    $env:PYTHONPATH="D:\\project\\mzxy\\mzxyAutoScript"
    toolkit\\python.exe tests\\bag_find_quiz_ticket.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.base.utils.utils import save_image
from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from tasks.Quiz.script_task import BAG_ITEM_NAME, ScriptTask

SHOT_DIR = ROOT / 'log' / 'bag_test'


def snapshot(task, name):
    path = SHOT_DIR / f'{name}.png'
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    save_image(task.device.image, str(path))
    print(f'  screenshot -> {path}')


def main() -> int:
    config = Config('oas1')
    # 曾经因设备上残留的旧 minitouch 进程不发 BTN_TOUCH（点击被系统判成 hover）而临时
    # 改用 adb tap；重启 atx-agent 后已恢复，这里跟随配置走 minitouch。
    device = Device(config)
    task = ScriptTask(config, device)

    print('=== 1. 打开背包（进背包页 / 重置到顶部 -> 整理）===')
    t0 = time.time()
    task.bag_open_ready()
    print(f'  耗时 {time.time() - t0:.1f}s')
    snapshot(task, 'bag_first_page')

    print('=== 2. 模板匹配找答题券 ===')
    t0 = time.time()
    coord = task.bag_find_by_rule(task.I_BAG_QUIZ_TICKET, BAG_ITEM_NAME)
    print(f'  模板结果: {coord}  耗时 {time.time() - t0:.1f}s')
    snapshot(task, 'after_template_scan')

    if coord is None:
        print('=== 2b. 模板没命中，回到顶部后走 OCR 兜底 ===')
        task.bag_scroll_to_top()
        t0 = time.time()
        coord = task.bag_find_by_ocr(BAG_ITEM_NAME)
        print(f'  OCR 结果: {coord}  耗时 {time.time() - t0:.1f}s')
        snapshot(task, 'after_ocr_scan')

    if coord is None:
        print('未找到答题券', file=sys.stderr)
        return 1

    print('=== 3. 点开详情面板确认 ===')
    logger.info(f'点击 ({coord[0]},{coord[1]})')
    task.device.click(x=coord[0], y=coord[1], control_name='test_bag_quiz_ticket')
    task.device.click_record_clear()
    opened = task.bag_wait_panel()
    task.screenshot()
    snapshot(task, 'item_panel')
    texts = [item.ocr_text for item in
             task.O_BAG_GRID.detect_and_ocr(task.device.image, logDisplay=False)]
    joined = ' | '.join(t.strip() for t in texts if t.strip())
    print(f'  面板出现: {opened}')
    print(f'  面板文本: {joined[:300]}')
    hit_name = BAG_ITEM_NAME in joined or any('答题' in t for t in texts)
    print(f'  面板里能读到「答题」: {hit_name}')

    print('=== 4. 关掉面板（不点立即使用）===')
    task.bag_close_item_panel()
    task.screenshot()
    snapshot(task, 'after_close')

    ok = opened and hit_name
    print('=== 结果:', 'PASS' if ok else 'FAIL', '===')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
