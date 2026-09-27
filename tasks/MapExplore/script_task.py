# This Python file uses the following encoding: utf-8
"""
探索主页面地图：采样所有可移动坐标并绘制成图（MapExplore）。

思路（前沿探索 / frontier exploration）：
- 把地图按 GRID 切成方格，维护三种状态：可行走、障碍、未知；
- 记录角色到过的每个坐标（`map_move_to` 的 on_position 回调），它所在的格子一定可行走；
- 每次挑一个「紧挨着已知区域」的未知格子（前沿），用 `map_move_to` 走过去：
  走到了 -> 可行走；走不到 -> 先记一次失败，达到重试上限才判为障碍
  （换个方向或许能绕过去，所以不一次就判死）；
- 前沿空了就说明可达区域都走完了；同时受 MAX_MINUTES 时间上限保护；
- 结束后把结果渲染成 PNG（可行走=浅色、障碍=暗红、采样点=绿、起点=黄）并保存 JSON。

参数都在下面，按需改。移动精度有限（到达容差见 move.py），GRID 不要太小。
"""
import json
import time
from datetime import datetime
from pathlib import Path

from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi

# ============================== 探索参数 ==============================
GRID = 15                # 网格分辨率（地图坐标单位）
MAX_MINUTES = 20         # 最长探索时间（分钟）
CELL_TOLERANCE = 6       # 到达格子的容差（应 < GRID/2，否则可能算错格子）
MOVE_TIMEOUT = 8         # 单次移动超时（秒）
MAX_RETRY = 2            # 一个格子最多从相邻已知点尝试几次才判为障碍
STALL_LIMIT = 2          # 单次移动连续多少步没更接近就放弃（越小越快跳过障碍）
SCALE = 16               # 输出图片每格像素
MARGIN = 42              # 给世界坐标刻度留的边距
LABEL_STEP = 5           # 每隔多少格画一条带世界坐标刻度的主网格线
# 要在图上标注的点：[(名字, x, y), ...]（世界坐标，超出范围不画）
MARKERS = []
# 探索前是否先回主页面：限制副本（仙府九重天等）里当前就在地图上，设为 False 直接开探
ENSURE_MAIN = True
NEIGHBORS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / 'log' / 'map'
# =====================================================================


class ScriptTask(GameUi):

    def run(self):
        try:
            self.explore()
        except Exception as e:  # noqa: BLE001  探索中断也要尽量把已画的地图存下来
            logger.exception(e)
        raise TaskEnd('MapExplore')

    # ------------------------------------------------------------------ 状态
    def state_path(self, map_name):
        return OUT_DIR / f'{map_name}_grid{GRID}_state.json'

    def init_state(self, start):
        self.walkable = set()          # 已确认可行走的格子
        self.fail_count = {}           # 格子 -> 失败次数
        self.frontier = set()          # 待探索的未知格子
        self.samples = set()           # 角色到过的原始坐标 (x, y)
        self.start_cell = (start[1] // GRID, start[2] // GRID)
        self.cur_cell = self.start_cell
        # 载入上一次的探索进度，接着探索（多次运行可累积覆盖）
        path = self.state_path(start[0])
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                self.walkable = {tuple(c) for c in data.get('walkable_cells', [])}
                self.samples = {tuple(s) for s in data.get('samples', [])}
                self.fail_count = {tuple(int(v) for v in c.split(',')): int(n)
                                   for c, n in data.get('fail_count', {}).items()}
                logger.info(f'Load explore state [{path.name}]: '
                            f'walkable={len(self.walkable)} samples={len(self.samples)}')
            except Exception as e:  # noqa: BLE001
                logger.warning(f'Failed to load explore state: {e}')
        self.rebuild_frontier()
        self.record(*start)

    def rebuild_frontier(self):
        self.frontier = set()
        for cell in self.walkable:
            for n in self.neighbors(cell):
                if n not in self.walkable and self.fail_count.get(n, 0) < MAX_RETRY:
                    self.frontier.add(n)

    def cell_of(self, x, y):
        return x // GRID, y // GRID

    def cell_center(self, cell):
        return cell[0] * GRID + GRID // 2, cell[1] * GRID + GRID // 2

    def record(self, name, x, y):
        """角色到过 (x,y)：采样下来，并把所在格子标为可行走"""
        self.samples.add((x, y))
        cell = self.cell_of(x, y)
        self.cur_cell = cell
        self.mark_walkable(cell)

    def mark_walkable(self, cell):
        if cell in self.walkable:
            return
        self.walkable.add(cell)
        self.frontier.discard(cell)
        self.fail_count.pop(cell, None)
        for n in self.neighbors(cell):
            if n in self.walkable:
                continue
            # 之前从别的相邻点判过障碍，但换到这里可能能过去，允许重试
            if self.fail_count.get(n, 0) < MAX_RETRY:
                self.frontier.add(n)

    def mark_blocked(self, cell):
        if cell in self.walkable:
            return
        self.fail_count[cell] = self.fail_count.get(cell, 0) + 1
        self.frontier.discard(cell)

    def neighbors(self, cell):
        ix, iy = cell
        return [(ix + dx, iy + dy) for dx, dy in NEIGHBORS4]

    def blocked_cells(self):
        return {cell for cell, count in self.fail_count.items() if count >= MAX_RETRY}

    def pick_frontier(self):
        """挑离当前位置最近的前沿格子"""
        best, best_d = None, 1e9
        cx, cy = self.cur_cell
        for cell in self.frontier:
            if cell in self.walkable:
                continue
            d = abs(cell[0] - cx) + abs(cell[1] - cy)
            if d < best_d:
                best_d, best = d, cell
        return best

    # ------------------------------------------------------------------ 主流程
    def explore(self):
        logger.hr('Explore map')
        if ENSURE_MAIN and not self.map_move_ensure_main_page():
            logger.error('Not on main page, cannot explore')
            return
        start = self.map_move_read_pos()
        if start is None:
            logger.error('Cannot read start position')
            return
        self.map_name = start[0]
        self.init_state(start)
        logger.info(f'Explore [{self.map_name}] grid={GRID} from {start[1]},{start[2]}')

        deadline = time.time() + MAX_MINUTES * 60
        step = 0
        while time.time() < deadline:
            target = self.pick_frontier()
            if target is None:
                logger.info('No frontier left, reachable area fully explored')
                break
            tx, ty = self.cell_center(target)
            step += 1
            logger.info(f'--- step {step}: cell {target} center ({tx},{ty}) '
                        f'walkable={len(self.walkable)} frontier={len(self.frontier)} '
                        f'blocked={len(self.blocked_cells())} ---')
            ok = self.map_move_to(tx, ty, tolerance=CELL_TOLERANCE, timeout=MOVE_TIMEOUT,
                                  ensure_main=False, calibrate=False, on_position=self.record,
                                  stall_limit=STALL_LIMIT)
            if self.reached(target, ok):
                self.mark_walkable(target)
            else:
                self.mark_blocked(target)

        self.report()

    def reached(self, target, ok):
        """判断是否真的到了目标格子（move_to 的成功或在失败后重新读位置）"""
        if ok:
            return True
        pos = self.map_move_read_pos()
        if pos is None:
            return False
        self.record(*pos)
        return self.cell_of(pos[1], pos[2]) == target

    # ------------------------------------------------------------------ 输出
    def report(self):
        self.save_state()
        blocked = self.blocked_cells()
        logger.hr('Map explore result')
        logger.info(f'[{self.map_name}] grid={GRID} '
                    f'walkable={len(self.walkable)} blocked={len(blocked)} samples={len(self.samples)}')
        if not self.walkable:
            logger.warning('Nothing explored')
            return
        xs = [c[0] for c in self.walkable]
        ys = [c[1] for c in self.walkable]
        logger.info(f'world x [{min(xs) * GRID}, {max(xs) * GRID + GRID}) '
                    f'y [{min(ys) * GRID}, {max(ys) * GRID + GRID})')

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = OUT_DIR / f'{self.map_name}_grid{GRID}_{stamp}'
        self.save_json(base.with_suffix('.json'), blocked)
        self.render_png(base.with_suffix('.png'), blocked)
        logger.info(f'Map saved: {base}.png / {base}.json')

    def save_state(self):
        """保存探索进度（下次运行接着探索）"""
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            'map_name': self.map_name,
            'grid': GRID,
            'start_cell': self.start_cell,
            'walkable_cells': sorted(self.walkable),
            'fail_count': {f'{c[0]},{c[1]}': n for c, n in self.fail_count.items()},
            'samples': sorted(self.samples),
        }
        self.state_path(self.map_name).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    def save_json(self, path, blocked):
        data = {
            'map_name': self.map_name,
            'grid': GRID,
            'start_cell': self.start_cell,
            'walkable_cells': sorted(self.walkable),
            'blocked_cells': sorted(blocked),
            'samples': sorted(self.samples),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    def render_png(self, path, blocked):
        from PIL import Image, ImageDraw, ImageFont

        xs = [c[0] for c in self.walkable]
        ys = [c[1] for c in self.walkable]
        min_ix, max_ix = min(xs), max(xs)
        min_iy, max_iy = min(ys), max(ys)
        w = max_ix - min_ix + 1
        h = max_iy - min_iy + 1
        origin_x = min_ix * GRID      # 左上角格子的世界坐标 x
        origin_y = min_iy * GRID
        right_pad, bottom_pad = 40, 20

        img = Image.new('RGB',
                        (MARGIN + w * SCALE + right_pad, MARGIN + h * SCALE + bottom_pad),
                        (28, 28, 34))
        draw = ImageDraw.Draw(img)

        def cell_box(cell):
            ix, iy = cell
            x = MARGIN + (ix - min_ix) * SCALE
            y = MARGIN + (iy - min_iy) * SCALE
            return x, y, x + SCALE, y + SCALE

        def in_range(cell):
            return min_ix <= cell[0] <= max_ix and min_iy <= cell[1] <= max_iy

        # 填色：试过走不到（暗红/橙） -> 可行走（浅绿白） -> 采样点（绿） -> 起点（黄）
        for cell, count in self.fail_count.items():
            if in_range(cell):
                color = (150, 40, 40) if count >= MAX_RETRY else (120, 80, 40)
                draw.rectangle(cell_box(cell), fill=color)
        for cell in self.walkable:
            draw.rectangle(cell_box(cell), fill=(205, 225, 205))
        for x, y in self.samples:
            cell = (x // GRID, y // GRID)
            if cell in self.walkable:
                draw.rectangle(cell_box(cell), fill=(90, 200, 120))
        if self.start_cell:
            draw.rectangle(cell_box(self.start_cell), fill=(255, 210, 60))
        for _, mx, my in MARKERS:
            cell = (mx // GRID, my // GRID)
            if in_range(cell):
                x0, y0, x1, y1 = cell_box(cell)
                draw.rectangle((x0 + 3, y0 + 3, x1 - 3, y1 - 3), outline=(255, 60, 60), width=3)

        # 细网格线（每格）
        thin = (70, 70, 80)
        for ix in range(w + 1):
            x = MARGIN + ix * SCALE
            draw.line([(x, MARGIN), (x, MARGIN + h * SCALE)], fill=thin)
        for iy in range(h + 1):
            y = MARGIN + iy * SCALE
            draw.line([(MARGIN, y), (MARGIN + w * SCALE, y)], fill=thin)

        # 主网格线 + 世界坐标刻度（每 LABEL_STEP 格一条，四边都标数字）
        try:
            font = ImageFont.truetype('arial.ttf', 13)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        major = (210, 210, 220)
        tick = (255, 230, 120)
        for ix in range(0, w + 1, LABEL_STEP):
            x = MARGIN + ix * SCALE
            draw.line([(x, MARGIN), (x, MARGIN + h * SCALE)], fill=major)
            label = str(origin_x + ix * GRID)
            draw.text((x + 2, 3), label, fill=tick, font=font)
            draw.text((x + 2, MARGIN + h * SCALE + 3), label, fill=tick, font=font)
        for iy in range(0, h + 1, LABEL_STEP):
            y = MARGIN + iy * SCALE
            draw.line([(MARGIN, y), (MARGIN + w * SCALE, y)], fill=major)
            label = str(origin_y + iy * GRID)
            draw.text((2, y + 2), label, fill=tick, font=font)
            draw.text((MARGIN + w * SCALE + 4, y + 2), label, fill=tick, font=font)

        img.save(path)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
