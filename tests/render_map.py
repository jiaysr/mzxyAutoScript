# This Python file uses the following encoding: utf-8
"""
从 MapExplore 保存的状态 JSON 重新渲染地图（不连设备、不移动角色）。

用法：
    $env:PYTHONPATH="D:\project\mzxy\mzxyAutoScript"
    toolkit\python.exe tests\render_map.py [状态json路径] [输出png路径]

默认读 log/map/蓬莱仙岛_grid15_state.json，输出同名的 _grid.png。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.MapExplore import script_task as me


class _Dummy:
    pass


def render(state_path: Path, png_path: Path):
    data = json.loads(state_path.read_text(encoding='utf-8'))
    obj = _Dummy()
    obj.walkable = {tuple(c) for c in data.get('walkable_cells', [])}
    obj.samples = {tuple(s) for s in data.get('samples', [])}
    obj.fail_count = {tuple(int(v) for v in k.split(',')): int(n)
                      for k, n in data.get('fail_count', {}).items()}
    obj.start_cell = tuple(data['start_cell']) if data.get('start_cell') else None
    me.ScriptTask.render_png(obj, png_path, set())
    print(f'rendered {state_path.name} -> {png_path}')


if __name__ == '__main__':
    state = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / 'log' / 'map' / '蓬莱仙岛_grid15_state.json'
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else state.with_name(state.stem + '_grid.png')
    render(state, out)
