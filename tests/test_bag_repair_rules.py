# -*- coding: utf-8 -*-
"""
背包修理装备模板规则离线校验（不连设备）

用真实截图验证：每个模板只在自己的状态下命中，互不误判。

    toolkit\\python.exe tests\\test_bag_repair_rules.py [截图目录]

默认从 MuMu 截图目录读这 4 张：
    103645 随身功能弹窗 / 103658 修理确认 / 103817 修理成功 / 103821 无需修理
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.base.utils.utils import load_image
from tasks.GameUi.assets import GameUiAssets

SHOT_DIR = r"C:\Users\wu179\Documents\MuMu共享文件夹\Screenshots"

# (截图文件, 期望命中的规则名)
CASES = [
    ("MuMu-20261101-103645-229.png", "I_BAG_MENU_REPAIR"),
    ("MuMu-20261101-103658-013.png", "I_BAG_REPAIR_CONFIRM_TEXT"),
    ("MuMu-20261101-103817-678.png", "I_BAG_REPAIR_SUCCESS"),
    ("MuMu-20261101-103821-512.png", "I_BAG_REPAIR_NOTHING"),
]
RULES = ["I_BAG_MENU_REPAIR", "I_BAG_REPAIR_CONFIRM_TEXT",
         "I_BAG_REPAIR_SUCCESS", "I_BAG_REPAIR_NOTHING"]


def score(image: np.ndarray, rule) -> float:
    """在 rule.roi_back 范围内做模板匹配，返回最高分（不改规则状态）"""
    x, y, w, h = rule.roi_back
    src = image[int(y):int(y) + int(h), int(x):int(x) + int(w)]
    mat = rule.image
    if mat.shape[0] > src.shape[0] or mat.shape[1] > src.shape[1]:
        return -1.0
    return float(cv2.matchTemplate(src, mat, cv2.TM_CCOEFF_NORMED).max())


def main() -> int:
    shot_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(SHOT_DIR)
    missing = [fn for fn, _ in CASES if not (shot_dir / fn).exists()]
    if missing:
        print(f'截图缺失，跳过：{[str(shot_dir / m) for m in missing]}')
        return 0

    failed = 0
    print(f'{"截图":>32} ' + ' '.join(f'{r.replace("I_BAG_", ""):>12}' for r in RULES))
    for fn, expect in CASES:
        image = load_image(str(shot_dir / fn))
        row, best_name, best_score = [], None, -1.0
        for name in RULES:
            rule = getattr(GameUiAssets, name)
            s = score(image, rule)
            row.append(f'{s:>12.3f}')
            if s > best_score:
                best_name, best_score = name, s
        ok = best_name == expect and best_score >= 0.8
        failed += 0 if ok else 1
        print(f'{fn[-12:]:>32} ' + ' '.join(row) + ('  OK' if ok else '  **FAIL**'))

    total = len(CASES) + 3
    failed += check_branches(shot_dir)
    print(f'\n{total - failed}/{total} 通过（阈值 0.8，且最高分须落在期望规则上）')
    return 1 if failed else 0


def make_task(shot_dir: Path, fn: str):
    """构造一个只喂单帧截图的任务实例，用于验证分支判断"""
    from module.config.config import Config
    from tasks.GameUi.navigation.general import GeneralFunctions

    image = load_image(str(shot_dir / fn))
    device = SimpleNamespace(image=image, sleep=lambda s: None,
                             click=lambda **kw: None, click_record_clear=lambda: None,
                             stuck_record_clear=lambda: None)
    task = GeneralFunctions(Config('oas1'), device)
    task.screenshot = lambda *a, **k: None      # device.image 已是固定帧
    task.reset_records = lambda: None
    return task


def check_branches(shot_dir: Path) -> int:
    """
    分支判断：确认框 -> 'confirm'，无需修理 -> 'nothing'，弹窗态 -> 菜单直接打开
    """
    failed = 0
    cases = [
        ("MuMu-20261101-103658-013.png", "bag_click_repair", 'confirm'),
        ("MuMu-20261101-103821-512.png", "bag_click_repair", 'nothing'),
        ("MuMu-20261101-103645-229.png", "bag_open_general_menu", True),
    ]
    for fn, method, expect in cases:
        got = getattr(make_task(shot_dir, fn), method)()
        ok = got == expect
        failed += 0 if ok else 1
        print(f'  分支 {method:>18} <- {fn[-12:]} 期望 {expect} 实得 {got}  {"OK" if ok else "**FAIL**"}')
    return failed



if __name__ == '__main__':
    sys.exit(main())
