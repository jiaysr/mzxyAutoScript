# This Python file uses the following encoding: utf-8
"""
虚拟摇杆移动（MapMove）：

在主页面用左下角的圆形移动轮盘，把角色走到指定的地图坐标点。

- `map_move_to(x, y)`          朝目标地图坐标移动（读右上角坐标闭环校正，直到到达）
- `map_move_calibrate()`       校准「屏幕拖动方向 -> 地图坐标变化」的关系（会让角色移动几秒）
- `map_move_ensure_main_page()` 移动前确保在主页面（只有主页面有轮盘）

坐标系：右上角显示的地图坐标（如 蓬莱仙岛188,127），由 `map_current_location()` 读取。

为什么需要校准：游戏是 2.5D 斜视角，屏幕拖动方向与地图坐标轴之间有一个旋转/缩放关系，
速度也未知。所以先朝两个互相垂直的屏幕方向各满偏移动 MOVE_PROBE_TIME 秒，测出
「屏幕单位方向 -> 地图坐标每秒变化」的 2x2 线性映射，再反解出目标方向对应的屏幕拖动
方向与持续时间，循环逼近目标。

真机实测后把 MOVE_CALIBRATION 常量填上，任务里就无需每次校准。
"""
import math

from module.base.timer import Timer
from module.exception import GameNotRunningError, GamePageUnknownError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class MapMove(BaseTask, GameUiAssets):
    # 移动轮盘圆心（主页面左下角虚拟摇杆，2026-09-26 真机实测）
    MOVE_WHEEL_CENTER = (193, 520)
    # 轮盘满偏拖动半径（像素）
    MOVE_WHEEL_RADIUS = 85
    # 校准探测：朝一个屏幕方向满偏移动的时间（秒）
    MOVE_PROBE_TIME = 1.5
    # 单次闭环移动的最长时间（秒），防止一步走太远
    MOVE_STEP_MAX = 1.2
    # 单次闭环移动的最短时间（秒），太短可能还没起步
    MOVE_STEP_MIN = 0.05
    # 移动步长保守系数（<1 少走一点，避免冲过目标来回震荡）
    MOVE_GAIN = 0.85
    # 判定到达目标的坐标容差
    MOVE_TOLERANCE = 5
    # 连续多少步没有更接近目标就判定为走不到（阻挡/边界），提前结束
    MOVE_STALL_LIMIT = 5
    # 没更接近目标时把步长缩小到这个系数，便于精细逼近
    MOVE_SHRINK = 0.6
    # 移动/探测结束后等待坐标刷新
    MOVE_SETTLE = 0.4
    # 读取坐标的重试次数（标签偶尔读不出来）
    MOVE_READ_RETRY = 3

    # 校准结果：((a, b), (c, d)) = 「屏幕 +X（右）」「屏幕 +Y（下）」方向满偏时
    # 每秒的地图坐标变化 (dx, dy)。为 None 时 map_move_to 会自动校准。
    # 下面是在「蓬莱仙岛」两次实测的平均值（2026-09-26）：
    #   屏幕右  -> 地图 (+10.0, -0.33)/s
    #   屏幕下  -> 地图 (-1.0, +18.0)/s
    # 即地图 x 向右、y 向下，但 x/y 的像素速度不同（y 明显更快）。
    MOVE_CALIBRATION = ((10.0, -1.0), (-0.33, 18.0))

    # ------------------------------------------------------------------ 基础操作
    def map_move_ensure_main_page(self, timeout: int = 30) -> bool:
        """
        确保停留在主页面（只有主页面才有移动轮盘），并关掉会盖住轮盘的主页面弹窗
        """
        from tasks.GameUi.page import page_main

        if not self.device.app_is_running():
            logger.warning('Game is not running, cannot move')
            return False
        self.ui_reset_current_page()
        try:
            if not self.ui_goto(page_main, timeout=timeout):
                logger.warning('Failed to go back to the main page')
                return False
        except (GameNotRunningError, GamePageUnknownError) as e:
            logger.warning(f'Main page is not reachable ({e})')
            return False
        self.map_close_main_popup()
        return True

    def map_move_read_pos(self, retry: int = None):
        """
        读取当前位置（地点名, x, y），读不到返回 None
        """
        retry = retry or self.MOVE_READ_RETRY
        for _ in range(retry):
            location = self.map_current_location()
            if location is not None:
                return location
            self.device.sleep(0.2)
        return None

    def map_hold_direction(self, direction, hold: float) -> None:
        """
        把轮盘朝 direction 方向满偏拖动并按住 hold 秒
        :param direction: 屏幕方向 (dx, dy)，只取方向，长度归一化
        :param hold: 按住时长（秒）
        """
        dx, dy = direction
        norm = math.hypot(dx, dy)
        if norm < 1e-6:
            return
        cx, cy = self.MOVE_WHEEL_CENTER
        px = int(round(cx + self.MOVE_WHEEL_RADIUS * dx / norm))
        py = int(round(cy + self.MOVE_WHEEL_RADIUS * dy / norm))
        self.device.hold_drag((cx, cy), (px, py), hold=hold, control_name='MAP_MOVE')
        self.device.click_record_clear()

    # ------------------------------------------------------------------ 校准
    def map_move_calibrate(self, probe_time: float = None):
        """
        校准轮盘方向与地图坐标的关系（会让角色移动几秒）

        朝屏幕 +X（右）和 +Y（下）各满偏移动 probe_time 秒，记录坐标变化，
        得到 2x2 映射矩阵，并打印可直接粘贴到 MOVE_CALIBRATION 的常量。

        :return: ((a, b), (c, d))；失败返回 None
        """
        logger.hr('Calibrate movement wheel')
        probe_time = probe_time or self.MOVE_PROBE_TIME
        if self.map_move_read_pos() is None:
            logger.error('Cannot read position for calibration')
            return None

        columns = []
        for direction, label in (((1, 0), 'screen +X (right)'), ((0, 1), 'screen +Y (down)')):
            before = self.map_move_read_pos()
            if before is None:
                logger.error('Cannot read position before probe')
                return None
            self.map_hold_direction(direction, probe_time)
            self.device.sleep(self.MOVE_SETTLE)
            after = self.map_move_read_pos()
            if after is None:
                logger.error('Cannot read position after probe')
                return None
            dx = (after[1] - before[1]) / probe_time
            dy = (after[2] - before[2]) / probe_time
            logger.attr(f'Wheel probe {label}',
                        f'{before[1]},{before[2]} -> {after[1]},{after[2]} => ({dx:.2f}, {dy:.2f})/s')
            columns.append((dx, dy))

        if self.map_move_speed(columns) < 1e-6:
            logger.error(f'Movement seems not working, probe result {columns}')
            return None
        # columns[0] = +X 探测的 (dx, dy)/s，columns[1] = +Y 探测的 (dx, dy)/s
        # 映射矩阵按 map_move_to 的约定存成 ((∂x/∂sx, ∂x/∂sy), (∂y/∂sx, ∂y/∂sy))
        (ax, ay), (bx, by) = columns
        self.MOVE_CALIBRATION = ((ax, bx), (ay, by))
        logger.info(f'MOVE_CALIBRATION = {self.MOVE_CALIBRATION}')
        return self.MOVE_CALIBRATION

    @staticmethod
    def map_move_det(m):
        """映射矩阵行列式（为 0 说明两个探测方向在坐标上共线，无法反解）"""
        return m[0][0] * m[1][1] - m[0][1] * m[1][0]

    @staticmethod
    def map_move_speed(m) -> float:
        """映射矩阵的「最大移动速度」量级：两个探测方向位移的最大值"""
        return max(math.hypot(*m[0]), math.hypot(*m[1]))

    # ------------------------------------------------------------------ 移动
    def map_move_to(self, x: int, y: int, map_name: str = None,
                    tolerance: int = None, timeout: int = 30,
                    ensure_main: bool = True, calibrate: bool = True,
                    on_position=None, stall_limit: int = None) -> bool:
        """
        走到地图坐标 (x, y)（在当前地图内移动，跨地图请先用 map_teleport 传送）

        :param x: 目标地图坐标 x
        :param y: 目标地图坐标 y
        :param map_name: 期望所在地图名，不匹配直接失败（可选）
        :param tolerance: 到达容差，默认 MOVE_TOLERANCE
        :param timeout: 超时（秒）
        :param ensure_main: 移动前先确保在主页面
        :param calibrate: 没有 MOVE_CALIBRATION 时是否自动校准
        :param on_position: 每读到一个位置就回调 on_position(name, x, y)（探索地图用，
                            任何角色站过的坐标都是可行走的）
        :param stall_limit: 连续多少步没更接近就放弃，默认 MOVE_STALL_LIMIT
        :return: 到达返回 True
        """
        logger.hr(f'Move to ({x},{y})')
        tolerance = tolerance if tolerance is not None else self.MOVE_TOLERANCE
        stall_limit = stall_limit if stall_limit is not None else self.MOVE_STALL_LIMIT

        if ensure_main and not self.map_move_ensure_main_page():
            return False

        matrix = self.MOVE_CALIBRATION
        if matrix is None and calibrate:
            matrix = self.map_move_calibrate()
        if matrix is None:
            logger.error('No movement calibration available')
            return False
        det = self.map_move_det(matrix)
        if abs(det) < 1e-6:
            logger.error(f'Movement calibration matrix is singular: {matrix}')
            return False

        timer = Timer(timeout).start()
        best = float('inf')
        stall = 0
        step_scale = 1.0
        while not timer.reached():
            self.reset_records()
            pos = self.map_move_read_pos()
            if pos is None:
                logger.warning('Cannot read position while moving')
                self.device.sleep(0.5)
                continue

            name, cx, cy = pos
            if map_name and not self.map_name_match(name, map_name):
                logger.warning(f'Not on map [{map_name}] (now [{name}]), stop moving')
                return False
            if on_position:
                on_position(name, cx, cy)

            dx, dy = x - cx, y - cy
            distance = math.hypot(dx, dy)
            logger.attr('Move position', f'{name}{cx},{cy} -> ({x},{y}) dist {distance:.1f}')
            if distance <= tolerance:
                logger.info(f'Arrived at ({cx},{cy})')
                return True

            # 越走越远或原地打转：缩小步长重试，连续多步没有更接近就判定走不到
            if distance < best - 0.3:
                best = distance
                stall = 0
            else:
                stall += 1
                step_scale = max(0.4, step_scale * self.MOVE_SHRINK)
                if stall >= stall_limit:
                    logger.warning(f'Move stalled at {name}{cx},{cy}, best distance {best:.1f}')
                    return best <= tolerance

            # 反解屏幕方向：M * s = (dx, dy)  =>  s = M^-1 * (dx, dy)
            sx = (matrix[1][1] * dx - matrix[0][1] * dy) / det
            sy = (-matrix[1][0] * dx + matrix[0][0] * dy) / det
            norm = math.hypot(sx, sy)
            if norm < 1e-6:
                logger.warning('Zero movement direction')
                return False
            sx, sy = sx / norm, sy / norm

            speed = math.hypot(matrix[0][0] * sx + matrix[0][1] * sy,
                               matrix[1][0] * sx + matrix[1][1] * sy)
            if speed < 1e-6:
                logger.warning('Zero movement speed')
                return False

            hold = distance / speed * self.MOVE_GAIN * step_scale
            hold = max(self.MOVE_STEP_MIN, min(self.MOVE_STEP_MAX, hold))
            logger.info(f'Move direction ({sx:.2f},{sy:.2f}) speed {speed:.1f}/s '
                        f'hold {hold:.2f}s scale {step_scale:.2f}')
            self.map_hold_direction((sx, sy), hold)
            self.device.sleep(self.MOVE_SETTLE)

        pos = self.map_move_read_pos()
        if pos is None:
            logger.warning(f'Move to ({x},{y}) timeout, position unknown')
        else:
            if on_position:
                on_position(*pos)
            logger.warning(f'Move to ({x},{y}) timeout, now at {pos[0]}{pos[1]},{pos[2]}')
        return False
