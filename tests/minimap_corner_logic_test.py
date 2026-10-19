# This Python file uses the following encoding: utf-8
"""
小地图四角判定逻辑单测（不需要设备）。

覆盖：
- 双轴地图（四边箭头都存在过的场景）
- 仅横向可滚（只有 left/right 箭头）
- 仅竖向可滚（只有 top/bottom 箭头）
- 整张地图刚好放得下（无箭头，无需滚动）

用法：
    toolkit\\python.exe tests\\minimap_corner_logic_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.GameUi.world.map import MapNavigation as M


def run_case(name, corner, arrows, expect_absent, expect_reached, expect_drag):
    absent, drag, horizontal, vertical = M.map_corner_plan(corner, arrows)
    reached = not absent or not (absent & set(arrows))
    errors = []
    if absent != set(expect_absent):
        errors.append(f'absent {sorted(absent)} != {sorted(expect_absent)}')
    if reached != expect_reached:
        errors.append(f'reached {reached} != {expect_reached}')
    if drag != expect_drag:
        errors.append(f'drag {drag} != {expect_drag}')
    status = 'PASS' if not errors else 'FAIL'
    print(f'[{status}] {name}: arrows={sorted(arrows)} corner={corner} '
          f'absent={sorted(absent)} h={horizontal} v={vertical} reached={reached}')
    for error in errors:
        print(f'       {error}')
    return not errors


def main():
    ok = True
    # 双轴地图：左下角（top/right 箭头 = 已到左、下边界）
    ok &= run_case('双轴-左下已到', 'bottom_left', {'top', 'right'},
                   {'left', 'bottom'}, True, M.MAP_CORNER_DRAGS['bottom_left'])
    # 双轴地图：中间（四边都有箭头），需要拖动
    ok &= run_case('双轴-中间拖向左下', 'bottom_left',
                   {'top', 'right', 'bottom', 'left'},
                   {'left', 'bottom'}, False, M.MAP_CORNER_DRAGS['bottom_left'])
    # 双轴地图：右上角已到
    ok &= run_case('双轴-右上已到', 'top_right', {'left', 'bottom'},
                   {'top', 'right'}, True, M.MAP_CORNER_DRAGS['top_right'])
    # 仅横向：左侧边界只有 right 箭头
    ok &= run_case('横向-左边界', 'bottom_left', {'right'},
                   {'left'}, True, M.MAP_HORIZONTAL_DRAGS['left'])
    # 仅横向：中间有 left/right，去右上角（右边界）
    ok &= run_case('横向-中间去右上', 'top_right', {'left', 'right'},
                   {'right'}, False, M.MAP_HORIZONTAL_DRAGS['right'])
    # 仅横向：右边界只有 left 箭头
    ok &= run_case('横向-右边界', 'top_right', {'left'},
                   {'right'}, True, M.MAP_HORIZONTAL_DRAGS['right'])
    # 仅竖向：下边界只有 top 箭头
    ok &= run_case('竖向-下边界', 'bottom_left', {'top'},
                   {'bottom'}, True, M.MAP_VERTICAL_DRAGS['bottom'])
    # 仅竖向：上边界只有 bottom 箭头，去右上（上边界）
    ok &= run_case('竖向-上边界', 'top_right', {'bottom'},
                   {'top'}, True, M.MAP_VERTICAL_DRAGS['top'])
    # 仅竖向：中间有 top/bottom，去右上（上边界）
    ok &= run_case('竖向-中间去右上', 'top_right', {'top', 'bottom'},
                   {'top'}, False, M.MAP_VERTICAL_DRAGS['top'])
    # 无滚动：整张地图放得下
    ok &= run_case('无滚动-任意角', 'bottom_right', set(),
                   set(), True, None)

    # 拖动端点必须都在地图内容区内
    x, y, w, h = M.MAP_CONTENT_REGION
    for table_name, table in (('双轴', M.MAP_CORNER_DRAGS),
                              ('横向', M.MAP_HORIZONTAL_DRAGS),
                              ('竖向', M.MAP_VERTICAL_DRAGS)):
        for key, (start, end) in table.items():
            for point in (start, end):
                inside = x <= point[0] < x + w and y <= point[1] < y + h
                if not inside:
                    print(f'[FAIL] {table_name}[{key}] 端点 {point} 超出 {M.MAP_CONTENT_REGION}')
                    ok = False
    print('拖动端点范围检查:', 'PASS' if ok else 'FAIL')
    print('RESULT:', 'ALL PASS' if ok else 'FAILED')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
