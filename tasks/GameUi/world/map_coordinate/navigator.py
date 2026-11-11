import time
from typing import Callable

from module.exception import TaskFailed
from module.logger import logger
from tasks.GameUi.world.map_coordinate.calibration import (
    MapCalibration,
    TargetNotCoveredError,
    load_map_database,
)
from tasks.GameUi.world.map_coordinate.viewport import detect_map_viewport


class MapCoordinateNavigator:
    """Click-to-walk navigator backed by per-map minimap calibration."""

    def __init__(self, host, map_database=None,
                 viewport_detector: Callable = None, clock: Callable = None):
        self.host = host
        self.map_database = map_database
        self.viewport_detector = viewport_detector or detect_map_viewport
        self.clock = clock or time.monotonic

    def _fail(self, message: str):
        logger.error(message)
        raise TaskFailed(message)

    def _close_minimap_if_open(self) -> None:
        # A live GameUi has the image rule. Test hosts may omit it.
        appear = getattr(self.host, "appear", None)
        rule = getattr(self.host, "I_MAP_MINIMAP_WORLD_MAP", None)
        if not callable(appear) or rule is None:
            return
        self.host.screenshot()
        if appear(rule) and not self.host.map_close_minimap():
            self._fail("关闭小地图失败，无法读取当前位置")

    def _database(self) -> dict:
        if self.map_database is not None:
            return self.map_database
        return load_map_database()

    @staticmethod
    def _is_at(position, target, tolerance: int) -> bool:
        return (abs(int(position[1]) - target[0]) <= tolerance
                and abs(int(position[2]) - target[1]) <= tolerance)

    def _read_viewport(self):
        image = self.host.screenshot()
        viewport = self.viewport_detector(image)
        if viewport is None or viewport.confidence < 0.72:
            self._fail("无法可靠识别小地图视口边界")
        return viewport

    def _close_minimap_safely(self) -> None:
        try:
            if not self.host.map_close_minimap():
                logger.warning("小地图未能关闭，保留现场供任务失败处理")
        except Exception as error:  # noqa: BLE001 - 不覆盖原始寻路失败
            logger.warning(f"清理小地图弹窗失败: {error}")

    def go_to_coordinate(self, x: int, y: int, map_name: str = None,
                         timeout: float = 60, tolerance: int = 1,
                         no_progress_timeout: float = 5,
                         stable_time: float = 3,
                         poll_interval: float = 1) -> tuple:
        """Navigate to a map coordinate or raise TaskFailed on business failure."""
        target = (int(x), int(y))
        tolerance = max(0, int(tolerance))
        timeout = max(0.1, float(timeout))
        no_progress_timeout = max(0.1, float(no_progress_timeout))
        stable_time = max(0.0, float(stable_time))
        poll_interval = max(0.05, float(poll_interval))

        self._close_minimap_if_open()
        start = self.host.map_move_read_pos()
        if start is None:
            self._fail("无法读取当前地图坐标，无法执行小地图寻路")
        current_map = str(start[0])
        if map_name and map_name != current_map:
            self._fail(f"当前地图是 {current_map}，目标地图是 {map_name}")
        map_name = map_name or current_map
        if self._is_at(start, target, tolerance):
            logger.info(f"已经在目标坐标 {map_name} {target[0]},{target[1]}")
            return start

        try:
            maps = self._database()
            map_data = maps.get(map_name)
            if not isinstance(map_data, dict) or not isinstance(map_data.get("views"), dict):
                self._fail(f"地图 {map_name} 没有坐标标定数据")
        except TaskFailed:
            raise
        except Exception as error:
            self._fail(f"读取地图坐标标定失败: {error}")

        if not self.host.map_open_minimap_tap():
            self._fail("打开小地图失败")
        minimap_open = True
        try:
            viewport = self._read_viewport()
            calibration = MapCalibration.fit(map_name, map_data["views"], viewport)
            view_name, _ = calibration.select_view(target, viewport)
            if not self.host.map_pan_to_corner(view_name):
                self._fail(f"小地图无法平移到 {view_name} 视图")

            viewport = self._read_viewport()
            calibration = MapCalibration.fit(map_name, map_data["views"], viewport)
            point = calibration.screen_for(view_name, target, viewport)
            if not (viewport.left + 8 <= point[0] < viewport.right - 8
                    and viewport.top + 8 <= point[1] < viewport.bottom - 8):
                raise TargetNotCoveredError(f"{view_name} 视图不再覆盖目标 {target}")
            point = (round(point[0]), round(point[1]))

            logger.info(f"小地图寻路 [{map_name}] -> ({target[0]},{target[1]})，"
                        f"视图={view_name}，点击像素=({point[0]},{point[1]})")
            self.host.device.click_adb(int(point[0]), int(point[1]))
            self.host.device.click_record_clear()
            self.host.device.sleep(0.2)
            if not self.host.map_close_minimap():
                self._fail(f"小地图点选后无法关闭，目标 {map_name} {target[0]},{target[1]}")
            minimap_open = False
        except TargetNotCoveredError as error:
            self._fail(f"地图 {map_name} 的四角视口未覆盖目标 {target}: {error}")
        except TaskFailed:
            raise
        except Exception as error:  # noqa: BLE001 - 转换为任务业务失败
            self._fail(f"小地图坐标转换或点击失败: {error}")
        finally:
            if minimap_open:
                self._close_minimap_safely()

        clicked_at = self.clock()
        last_progress_at = clicked_at
        last_position = start
        has_progress = False
        stable_since = None
        while self.clock() - clicked_at <= timeout:
            now = self.clock()
            position = self.host.map_move_read_pos()
            if position is not None:
                if str(position[0]) != map_name:
                    self._fail(f"寻路期间地图改变: {position[0]} (目标地图 {map_name})")
                if self._is_at(position, target, tolerance):
                    if stable_since is None:
                        stable_since = now
                    elif now - stable_since >= stable_time:
                        logger.info(f"地图坐标到达 {map_name} {position[1]},{position[2]}")
                        return position
                else:
                    stable_since = None

                if position[1:3] != last_position[1:3]:
                    last_position = position
                    last_progress_at = now
                    has_progress = True
                elif has_progress and now - last_progress_at >= stable_time:
                    self._fail(f"坐标不可达: 目标 {map_name} {target[0]},{target[1]}，"
                               f"实际停在 {position[1]},{position[2]}")

            if not has_progress and now - clicked_at >= no_progress_timeout:
                actual = (position[1], position[2]) if position is not None else ("?", "?")
                self._fail(f"坐标不可达: 目标 {map_name} {target[0]},{target[1]}，"
                           f"人物未移动，当前 {actual[0]},{actual[1]}")
            self.host.device.sleep(poll_interval)

        actual = self.host.map_move_read_pos()
        actual_text = (f"{actual[1]},{actual[2]}" if actual else "未知")
        self._fail(f"地图寻路超时: 目标 {map_name} {target[0]},{target[1]}，实际 {actual_text}")


class MapCoordinateNavigation:
    """GameUi mixin exposing calibrated minimap coordinate navigation."""

    def map_go_to_coordinate(self, x: int, y: int, **kwargs) -> tuple:
        navigator = getattr(self, "_map_coordinate_navigator", None)
        if navigator is None:
            navigator = MapCoordinateNavigator(self)
            self._map_coordinate_navigator = navigator
        return navigator.go_to_coordinate(x, y, **kwargs)
