# This Python file uses the following encoding: utf-8
"""
虚拟摇杆移动（MapMove）：

在主页面用左下角的圆形移动轮盘，把角色走到指定的地图坐标点。

- `map_move_to(x, y)`          朝目标地图坐标移动（读右上角坐标闭环校正，直到到达）
- `map_move_path(points)`      按顺序走到多个坐标点，中途可被 stop_check 打断
- `map_move_calibrate()`       校准「屏幕拖动方向 -> 地图坐标变化」的关系（会让角色移动几秒）
- `map_move_ensure_main_page()` 移动前确保在主页面（只有主页面有轮盘）

坐标系：右上角显示的地图坐标（如 蓬莱仙岛188,127），由 `map_current_location()` 读取。

为什么需要校准：游戏是 2.5D 斜视角，屏幕拖动方向与地图坐标轴之间有一个旋转/缩放关系，
速度也未知。所以先朝两个互相垂直的屏幕方向各满偏移动 MOVE_PROBE_TIME 秒，测出
「屏幕单位方向 -> 地图坐标每秒变化」的 2x2 线性映射，再反解出目标方向对应的屏幕拖动
方向与持续时间，循环逼近目标。

**校准值按地图分别保存**（MOVE_CALIBRATION_MAP）：不同地图的缩放比例与斜视角不同，
校准值不通用（如蓬莱仙岛 y 轴 18 单位/s，域外迷窟实测约 24 单位/s），
用错地图的校准值会把角色走偏。真机实测后把常量填上，任务里就无需每次校准。

**精调（小距离）靠摇杆半偏降速**：满偏存在最小步长——`hold_drag_minitouch`
（module/device/method/minitouch.py:624）有 `hold = max(0.05, hold)` 硬下限，
加上 down 等待与 8 段拖动开销，满偏最小有效移动时间约 0.13~0.2s，
在 18~24 单位/s 下相当于一步 2.4~4.8 个坐标单位。所以距离小于 MOVE_FINE_DISTANCE
时改用 MOVE_FINE_RATIO 的偏转比例（速度按比例下降），把最小步长压到容差以内，
否则在目标两侧来回震荡永远收敛不了。
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

    # ------------------------------------------------------------------ 精调（小距离）
    # 距离小于该值时进入精调：改用小比例摇杆偏转降速
    MOVE_FINE_DISTANCE = 6
    # 精调时的轮盘偏转比例（相对 MOVE_WHEEL_RADIUS）
    MOVE_FINE_RATIO = 0.25
    # 偏转比例下限：低于这个值游戏摇杆进死区，角色不动
    MOVE_RATIO_MIN_DEAD = 0.15
    # 单次拖动的时间下限（秒），对齐 hold_drag_minitouch 的 0.05s 硬下限
    MOVE_HOLD_FLOOR = 0.05

    # ---------------------------------------------------------------- 精调能力边界（真机实测，重要）
    # 单步可控位移下限约 2 个坐标单位，**做不到 1 个单位的定向微调**。
    # 实测记录（tests/probe_fine_step.py，域外迷窟 2026-10-07，探测方向=屏幕 +X）：
    #   ratio=1.00 hold=0.05 -> 0.0 单位（满偏+最短时长落在起步加速期，角色不动）
    #   ratio=0.50 hold=0.05 -> 1.4 单位
    #   ratio=0.35 hold=0.05 -> 2.0 单位
    #   ratio=0.25 hold=0.05 -> 2.0 单位
    #   ratio=0.25 hold=0.10 -> 2.2 单位
    #   ratio=0.25 hold=0.20 -> 4.1 单位
    # 偏转比例在 0.25~0.5 之间对单步位移几乎无影响（下限由摇杆起步加速 +
    # hold_drag_minitouch 的 0.05s 硬下限决定），要更细只能靠 hold 时长，
    # 而时长是量化档位（0.05/0.10/0.20 之间跳 2 个单位）。
    MOVE_FINE_MIN_STEP_UNITS = 2.0
    # 收敛条件：最小步长必须明显小于容差，否则几何上追不上。
    # 取「单步位移 x 该系数 < 容差」作为精调可达的下限，
    # 即容差至少要比单步位移大 50% 才有收敛希望（2 单位步长 -> 容差需 > 3）。
    # 容差不满足时 map_move_to 会警告并按最佳距离返回 False，而不是空转到超时。
    MOVE_FINE_STEP_SAFETY = 1.5

    # 精调阶段的步长保守系数
    MOVE_FINE_GAIN = 0.8
    # 距离明显变小（比上一轮近了一半以上）时把步长系数恢复，避免一次震荡后
    # 一直锁在最小步长上磨（step_scale 原先只降不升，是个隐藏的性能坑）
    MOVE_SCALE_RESET_RATIO = 0.5

    # 校准结果（按地图分别保存）：((a, b), (c, d)) = 「屏幕 +X（右）」「屏幕 +Y（下）」
    # 方向满偏时每秒的地图坐标变化 (dx, dy)，即 matrix[0]=∂(x,y)/∂sx、matrix[1]=∂(x,y)/∂sy。
    #
    # 不同地图的缩放比例/斜视角不同，校准值不通用，必须分别实测：
    # - 蓬莱仙岛（2026-09-26 两次实测平均）：
    #     屏幕右 -> 地图 (+10.0, -0.33)/s
    #     屏幕下 -> 地图 (-1.0, +18.0)/s
    #   即地图 x 向右、y 向下，但 x/y 的移动速度不同（y 明显更快）。
    # - 域外迷窟（2026-10-07 实测 map_move_calibrate）：
    #     屏幕右 -> 地图 (+17.33, 0.00)/s
    #     屏幕下 -> 地图 (+0.67, +19.33)/s
    #
    # 新地图首次使用时跑 map_move_calibrate()，把日志打印的常量粘到这里即可免校准。
    MOVE_CALIBRATION_MAP = {
        '蓬莱仙岛': ((10.0, -1.0), (-0.33, 18.0)),
        '域外迷窟': ((17.33, 0.67), (0.0, 19.33)),
    }
    # 兜底校准（地图名未记录在上面时用）；None = 无兜底，必须现场校准
    MOVE_CALIBRATION = None

    # 本次进程内现场校准得到的矩阵：{地图名: 矩阵}
    # 只在内存里，不落类常量（校准值按地图区分，跨进程复用请填 MOVE_CALIBRATION_MAP）
    _runtime_calibration: dict = {}

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

    def map_hold_direction(self, direction, hold: float, ratio: float = 1.0) -> None:
        """
        把轮盘朝 direction 方向拖动并按住 hold 秒

        :param direction: 屏幕方向 (dx, dy)，只取方向，长度归一化
        :param hold: 按住时长（秒）
        :param ratio: 轮盘偏转比例（相对 MOVE_WHEEL_RADIUS），1.0 = 满偏。
                      小于 1 时摇杆偏转角变小，移动速度按比例下降，
                      用来做小距离精调（满偏最小步长太大，见 MOVE_FINE_RATIO 注释）
        """
        dx, dy = direction
        norm = math.hypot(dx, dy)
        if norm < 1e-6:
            return
        ratio = max(0.0, min(1.0, ratio))
        cx, cy = self.MOVE_WHEEL_CENTER
        px = int(round(cx + self.MOVE_WHEEL_RADIUS * ratio * dx / norm))
        py = int(round(cy + self.MOVE_WHEEL_RADIUS * ratio * dy / norm))
        self.device.hold_drag((cx, cy), (px, py), hold=hold, control_name='MAP_MOVE')
        self.device.click_record_clear()

    # ------------------------------------------------------------------ 校准
    def map_move_calibrate(self, probe_time: float = None, map_name: str = None):
        """
        校准轮盘方向与地图坐标的关系（会让角色移动几秒）

        朝屏幕 +X（右）和 +Y（下）各满偏移动 probe_time 秒，记录坐标变化，
        得到 2x2 映射矩阵，并打印可直接粘贴到 MOVE_CALIBRATION_MAP 的常量。

        :param probe_time: 单个方向的探测时长（秒）
        :param map_name: 校准结果归属的地图名，默认用当前所在地图
        :return: ((a, b), (c, d))；失败返回 None
        """
        logger.hr('Calibrate movement wheel')
        probe_time = probe_time or self.MOVE_PROBE_TIME
        start = self.map_move_read_pos()
        if start is None:
            logger.error('Cannot read position for calibration')
            return None
        map_name = map_name or start[0]
        logger.attr('Calibrate on map', f'{start[0]} ({start[1]},{start[2]})')

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
        matrix = ((ax, bx), (ay, by))
        # 只存进 runtime 字典（本次进程内该地图直接复用），不写 MOVE_CALIBRATION 兜底常量，
        # 否则这次校准的值会被当成通用兜底套用到别的地图上
        self._runtime_calibration[map_name] = matrix
        logger.info(f'MOVE_CALIBRATION = {matrix}')
        logger.info(f"# 粘到 MOVE_CALIBRATION_MAP: '{map_name}': {matrix},")
        return matrix

    def map_move_calibration_for(self, map_name: str):
        """
        取指定地图的校准矩阵：优先本次进程实测结果，其次 MOVE_CALIBRATION_MAP 常量，
        最后兜底 MOVE_CALIBRATION。都没有返回 None。
        """
        if map_name:
            matrix = self._runtime_calibration.get(map_name)
            if matrix is not None:
                return matrix
            matrix = self.MOVE_CALIBRATION_MAP.get(map_name)
            if matrix is not None:
                return matrix
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
        :param tolerance: 到达容差，默认 MOVE_TOLERANCE。传 0 表示精确到达
                          （会启用精调，但注意单步位移下限约 2 个坐标单位，
                          容差 < 2 时不保证命中，见 MOVE_FINE_MIN_STEP_UNITS 实测记录；
                          容差 >= 1 时距离已在容差内会直接返回 True）
        :param timeout: 超时（秒）
        :param ensure_main: 移动前先确保在主页面
        :param calibrate: 该地图没有校准值时是否自动校准
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

        # 已在容差内直接返回，不做任何移动（避免默认容差 5 时「站在 1 格外也判定到达」）
        # 先记录起点，循环里第一次读到位置时会用同一个判据再确认一次
        origin = self.map_move_read_pos()

        # 校准值按地图取（不同地图缩放/斜视角不同，不能跨地图套用）
        if origin is None:
            logger.error('Cannot read current position, cannot move')
            return False
        current_map = origin[0]
        matrix = self.map_move_calibration_for(current_map)
        if matrix is None and calibrate:
            logger.warning(f'No calibration for [{current_map}], calibrating now')
            matrix = self.map_move_calibrate(map_name=current_map)
        if matrix is None:
            logger.error('No movement calibration available')
            return False
        det = self.map_move_det(matrix)
        if abs(det) < 1e-6:
            logger.error(f'Movement calibration matrix is singular: {matrix}')
            return False

        # 收敛性预检：精调的单步位移约 MOVE_FINE_MIN_STEP_UNITS，容差必须明显大于它，
        # 否则每步都会从目标上跨过去，只能空转到超时（实测 tolerance=2 时
        # 在 dist 2.2~5.0 之间无限震荡 30s）。这里直接告知可行的最小容差。
        min_needed = self.MOVE_FINE_MIN_STEP_UNITS * self.MOVE_FINE_STEP_SAFETY
        fine_will_work = tolerance >= min_needed
        if not fine_will_work and math.hypot(x - origin[1], y - origin[2]) > tolerance:
            logger.warning(
                f'Tolerance {tolerance} is smaller than the minimum controllable step '
                f'({self.MOVE_FINE_MIN_STEP_UNITS} units), reaching exactly ({x},{y}) is '
                f'not reliable. Use tolerance >= {min_needed:.0f}, or expect best-effort '
                f'retry until timeout.')

        timer = Timer(timeout).start()
        best = float('inf')
        stall = 0
        step_scale = 1.0
        # 精调是单向锁存的：一旦进入就不再退回满偏。
        # 否则会在 MOVE_FINE_DISTANCE 阈值边界反复横跳（3.2 进精调 -> 挪过头成 4.1
        # -> 退出精调 -> 又挪过头），每次横跳都要花掉一轮截图+拖动，最后 2~3 格磨不完。
        fine_locked = False
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

            # 精调：距离很小时改用小比例摇杆偏转降速。满偏存在最小步长
            # （hold_drag_minitouch 有 0.05s 硬下限 + 摇杆起步加速，约 2 个坐标单位，
            # 实测见 MOVE_FINE_MIN_STEP_UNITS），目标只剩 1~2 格时一步就会冲过头。
            # 锁存后不再退回满偏，避免在阈值边界来回横跳浪费步数。
            if distance <= self.MOVE_FINE_DISTANCE:
                fine_locked = True
            fine = fine_locked
            # 剩余距离小于最小步长时，已经没有更细的拖动档位可选
            fine_step = distance <= self.MOVE_FINE_MIN_STEP_UNITS

            # 越走越远或原地打转：缩小步长重试，连续多步没有更接近就判定走不到
            if distance < best - 0.3:
                # 明显更接近了就把步长系数往回提，否则一次震荡后会一直锁在
                # 最小步长上慢慢磨（step_scale 只降不升会拖垮整段移动的耗时）
                if best != float('inf') and distance < best * self.MOVE_SCALE_RESET_RATIO:
                    step_scale = min(1.0, step_scale * 2)
                    logger.info(f'Closer, restore step scale to {step_scale:.2f}')
                best = distance
                stall = 0
            else:
                stall += 1
                step_scale = max(0.4, step_scale * self.MOVE_SHRINK)
                if stall >= stall_limit:
                    # 精调阶段：单步位移 >= 容差时继续重试也只是在目标两侧来回跨，
                    # 每轮都要花一次截图 + 拖动，纯浪费超时预算（实测 30s 空转）。
                    # 此时按当前实际距离判定一次就收工。
                    if fine:
                        if distance <= tolerance:
                            logger.info(f'Arrived at ({cx},{cy}) after fine retries')
                            return True
                        logger.warning(f'Fine move gave up at {name}{cx},{cy}, '
                                       f'distance {distance:.1f} > tolerance {tolerance} '
                                       f'(min step {self.MOVE_FINE_MIN_STEP_UNITS} units). '
                                       f'Use tolerance >= '
                                       f'{self.MOVE_FINE_MIN_STEP_UNITS * self.MOVE_FINE_STEP_SAFETY:.0f}.')
                        return False
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

            # 满偏时该方向上的移动速度（地图单位/秒）
            speed = math.hypot(matrix[0][0] * sx + matrix[0][1] * sy,
                               matrix[1][0] * sx + matrix[1][1] * sy)
            if speed < 1e-6:
                logger.warning('Zero movement speed')
                return False

            if fine:
                # 精调：唯一能调的是 hold 时长，不是偏转比例——实测小偏转几乎不降速
                # （摇杆死区，ratio 0.25 + hold 0.66s 实测走了 13 个单位），
                # 所以不做 speed *= ratio，那会让 hold 被严重低估而一步冲过头。
                ratio = max(self.MOVE_RATIO_MIN_DEAD, self.MOVE_FINE_RATIO)
                if distance <= fine_step:
                    # 已进入最小步长：只有「最短拖动」这一个选项，要么进去要么放弃，
                    # 按比例算 hold 只会更大。能否命中完全取决于 tolerance
                    # 是否大于单步位移（tolerance <= 单步位移 时几何上不可能收敛）。
                    hold = self.MOVE_HOLD_FLOOR
                else:
                    hold = distance / speed * self.MOVE_FINE_GAIN * step_scale
                    hold = max(self.MOVE_HOLD_FLOOR, min(self.MOVE_STEP_MAX, hold))
            else:
                ratio = 1.0
                hold = distance / speed * self.MOVE_GAIN * step_scale
                hold = max(self.MOVE_STEP_MIN, min(self.MOVE_STEP_MAX, hold))
            logger.info(f'Move direction ({sx:.2f},{sy:.2f}) speed {speed:.1f}/s '
                        f'hold {hold:.2f}s ratio {ratio:.2f} scale {step_scale:.2f}'
                        f'{" [fine]" if fine else ""}'
                        f'{" (fine min-step, may overshoot)" if fine and hold <= self.MOVE_HOLD_FLOOR + 1e-6 else ""}')
            self.map_hold_direction((sx, sy), hold, ratio=ratio)
            self.device.sleep(self.MOVE_SETTLE)

        pos = self.map_move_read_pos()
        if pos is None:
            logger.warning(f'Move to ({x},{y}) timeout, position unknown')
            return False
        if on_position:
            on_position(*pos)
        # 超时后按实际距离判定：容差内算到达（精调阶段最后一步可能正好压中）
        if math.hypot(x - pos[1], y - pos[2]) <= tolerance:
            logger.info(f'Arrived at ({pos[1]},{pos[2]}) on timeout')
            return True
        logger.warning(f'Move to ({x},{y}) timeout, now at {pos[0]}{pos[1]},{pos[2]}, '
                       f'best distance {min(best, math.hypot(x - pos[1], y - pos[2])):.1f}')
        return False

    def map_move_path(self, points: list, tolerance: int = None, timeout: int = 30,
                      ensure_main: bool = True, on_position=None) -> int:
        """
        按顺序走到多个坐标点（每点独立寻路，任一点失败即停止）

        用于「跑多个目标点」的场景（如依次巡逻若干位置），
        与 MapExplore 的前沿探索不同：这里点与点之间不做可达性判断。

        :param points: [(x, y), ...] 目标点列表
        :param tolerance: 到达容差，默认 MOVE_TOLERANCE
        :param timeout: 单个点的超时（秒）
        :param ensure_main: 移动前先确保在主页面
        :param on_position: 每读到一个位置就回调 on_position(name, x, y)
        :return: 成功走到的点数；中途失败返回已完成的点数
        """
        if not points:
            return 0
        logger.hr(f'Move path with {len(points)} point(s)')
        done = 0
        for index, (x, y) in enumerate(points):
            logger.attr(f'Path point {index + 1}/{len(points)}', f'({x},{y})')
            # 第一步才需要回主页面，后续点已经在主页面上了
            ok = self.map_move_to(x, y, tolerance=tolerance, timeout=timeout,
                                  ensure_main=ensure_main if index == 0 else False,
                                  on_position=on_position)
            if not ok:
                logger.warning(f'Path stopped at point {index + 1} ({x},{y}), done {done}')
                return done
            done += 1
        logger.info(f'Path finished, all {done} point(s) reached')
        return done
