"""按视口方向批量点击小地图并记录真实到达坐标。

工具流程（由 MapTest 的 record 动作调用）：

1. 回到主页面并读取当前地图名；
2. 对每个视口方向：打开小地图 -> 平移到该角 -> 校验点击像素在视口内；
3. 点击像素 -> 关闭小地图 -> 轮询坐标直到稳定；
4. 记录 `screen`（整屏点击像素）与 `coord`（稳定后读回的真实坐标）。

点击后角色没有移动、坐标始终读不到、或地图在录制期间改变时，该点失败；
已成功的点仍会返回，方便分多次补录。
"""
import time
from dataclasses import dataclass, field
from typing import Callable

from module.logger import logger
from tasks.GameUi.world.map_coordinate.calibration import VIEW_NAMES
from tasks.GameUi.world.map_coordinate.viewport import (
    MIN_VIEWPORT_CONFIDENCE,
    Viewport,
    detect_map_viewport,
)


@dataclass
class RecordResult:
    """一次录制会话的结果。"""

    map_name: str
    views: dict[str, list[dict]] = field(default_factory=dict)
    viewport: Viewport | None = None
    failed: list[dict] = field(default_factory=list)
    fatal_error: str | None = None
    # 本次使用的点击计划，原样随结果保存，便于复现和补录
    plan: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    @property
    def sample_count(self) -> int:
        return sum(len(samples) for samples in self.views.values())


class MapSampleRecorder:
    """Click minimap pixels and record the stabilized map coordinates."""

    def __init__(self, host, plan, stable_time: float = 2.0,
                 timeout: float = 60.0, no_progress_timeout: float = 8.0,
                 poll_interval: float = 0.5, click_settle: float = 0.2,
                 margin: int = 8,
                 viewport_detector: Callable = None,
                 clock: Callable = None):
        self.host = host
        self.plan = self.normalize_plan(plan)
        self.stable_time = max(0.0, float(stable_time))
        self.timeout = max(0.1, float(timeout))
        self.no_progress_timeout = max(0.1, float(no_progress_timeout))
        self.poll_interval = max(0.05, float(poll_interval))
        self.click_settle = max(0.0, float(click_settle))
        self.margin = max(0, int(margin))
        self.viewport_detector = viewport_detector or detect_map_viewport
        self.clock = clock or time.monotonic

    @staticmethod
    def normalize_plan(plan) -> dict[str, list[tuple[int, int]]]:
        """把 `{视图: [点击像素, ...]}` 统一成整数坐标列表。

        兼容 `[x, y]` 列表、`(x, y)` 元组和 `{"screen": [x, y]}` 对象；
        也接受外层带 `views` 的 JSON（如地图录制文件）。
        """
        if not isinstance(plan, dict):
            raise ValueError("录制计划必须是 {视图: 点击坐标列表} 的字典")
        if isinstance(plan.get("views"), dict):
            plan = plan["views"]
        normalized = {}
        for view_name, points in plan.items():
            if view_name not in VIEW_NAMES:
                raise ValueError(f"未知视口方向: {view_name}")
            if not isinstance(points, (list, tuple)) or not points:
                raise ValueError(f"视口 {view_name} 没有点击坐标")
            view_points = []
            for index, point in enumerate(points, start=1):
                if isinstance(point, dict):
                    point = point.get("screen")
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError(f"视口 {view_name} 第 {index} 个坐标格式无效: {point!r}")
                view_points.append((int(round(float(point[0]))),
                                    int(round(float(point[1])))))
            normalized[view_name] = view_points
        if not normalized:
            raise ValueError("录制计划为空")
        return normalized

    def record(self) -> RecordResult:
        """按计划顺序点击并等待坐标稳定，返回成功与失败明细。"""
        self._safe_close()
        try:
            ready = self.host.map_move_ensure_main_page()
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"无法回到主页面: {error}") from error
        if not ready:
            raise RuntimeError("无法回到主页面，录制中止")

        try:
            start = self.host.map_move_read_pos()
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"无法读取当前地图坐标: {error}") from error
        if start is None:
            raise RuntimeError("无法读取当前地图坐标，录制中止")
        map_name = str(start[0])
        current = start
        viewport = None
        views: dict[str, list[dict]] = {}
        failed: list[dict] = []
        fatal_error = None
        total = sum(len(points) for points in self.plan.values())
        index = 0

        for view_name, points in self.plan.items():
            for point in points:
                index += 1
                logger.info(f"录制 {index}/{total}: [{map_name}] "
                            f"{view_name} screen=({point[0]},{point[1]})")
                try:
                    self._open_and_pan(view_name)
                    detected = self.viewport_detector(self.host.screenshot())
                    if detected is None or detected.confidence < MIN_VIEWPORT_CONFIDENCE:
                        raise RuntimeError("无法可靠识别小地图视口边界")
                    if viewport is None:
                        viewport = detected
                    elif detected.rect != viewport.rect:
                        logger.warning(f"视口在不同角不一致: "
                                       f"{viewport.rect} -> {detected.rect}")

                    x, y = point
                    if not self._inside(detected, x, y):
                        raise RuntimeError(
                            f"点击点不在 {view_name} 视口安全区内: {detected.rect}")

                    self.host.device.click_adb(x, y)
                    self.host.device.click_record_clear()
                    self.host.device.sleep(self.click_settle)
                    if not self.host.map_close_minimap():
                        raise RuntimeError("小地图点选后无法关闭")

                    position = self._wait_stable(current)
                    if position is None:
                        raise RuntimeError("点击后坐标未移动，或未在超时内稳定")
                    if not self._same_map(position[0], map_name):
                        fatal_error = (f"地图在录制期间改变: {map_name} -> {position[0]}，"
                                       f"已停止后续录制")
                        logger.error(fatal_error)
                        break

                    views.setdefault(view_name, []).append({
                        "screen": [x, y],
                        "coord": [int(position[1]), int(position[2])],
                    })
                    current = position
                    logger.info(f"录制成功: {view_name} screen=({x},{y}) -> "
                                f"[{map_name}] {position[1]},{position[2]}")
                except Exception as error:  # noqa: BLE001 - 单点失败不影响后续补录
                    failed.append({"view": view_name, "screen": [point[0], point[1]],
                                   "error": str(error)})
                    logger.warning(f"录制失败: {view_name} screen=({point[0]},{point[1]}) - "
                                   f"{error}")
                    self._safe_close()
            if fatal_error:
                break

        self._safe_close()
        return RecordResult(map_name=map_name, views=views, viewport=viewport,
                            failed=failed, fatal_error=fatal_error, plan=self.plan)

    def _open_and_pan(self, view_name: str) -> None:
        if not self.host.map_open_minimap_tap():
            raise RuntimeError("打开小地图失败")
        if not self.host.map_pan_to_corner(view_name):
            raise RuntimeError(f"小地图无法平移到 {view_name} 视图")

    def _same_map(self, current_name, expected_name) -> bool:
        """优先用 GameUi 的地点名容错匹配；测试宿主退回精确比较。"""
        match = getattr(self.host, "map_name_match", None)
        if callable(match):
            return bool(match(str(current_name), str(expected_name)))
        return str(current_name) == str(expected_name)

    def _wait_stable(self, start):
        """轮询坐标：先确认角色移动，再等待连续 stable_time 秒不变。"""
        started = self.clock()
        last_coord = None
        stable_since = None
        moved = False
        while self.clock() - started < self.timeout:
            position = self.host.map_move_read_pos()
            now = self.clock()
            if position is None:
                # OCR 偶发读不到：不要用之前的时间戳误判稳定
                stable_since = None
            else:
                coord = (int(position[1]), int(position[2]))
                if coord != (int(start[1]), int(start[2])):
                    moved = True
                if coord == last_coord:
                    if stable_since is None:
                        stable_since = now
                    if moved and now - stable_since >= self.stable_time:
                        return position
                else:
                    last_coord = coord
                    stable_since = now
            if not moved and now - started >= self.no_progress_timeout:
                logger.warning("点击后角色没有移动")
                return None
            self.host.device.sleep(self.poll_interval)
        logger.warning("等待坐标稳定超时")
        return None

    def _inside(self, viewport: Viewport, x: int, y: int) -> bool:
        return (viewport.left + self.margin <= x < viewport.right - self.margin
                and viewport.top + self.margin <= y < viewport.bottom - self.margin)

    def _safe_close(self) -> None:
        try:
            self.host.map_close_minimap()
        except Exception as error:  # noqa: BLE001 - 清理失败不覆盖录制结果
            logger.warning(f"关闭小地图失败: {error}")
