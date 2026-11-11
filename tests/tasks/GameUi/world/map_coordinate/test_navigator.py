import numpy as np
import pytest

from module.exception import TaskFailed
from tasks.GameUi.world.map_coordinate.calibration import MapCalibration, ViewTransform
from tasks.GameUi.world.map_coordinate.navigator import MapCoordinateNavigator
from tasks.GameUi.world.map_coordinate.viewport import Viewport


SWAMP_SAMPLES = [
    {"screen": [542, 208], "coord": [223, 61]},
    {"screen": [750, 239], "coord": [347, 79]},
    {"screen": [286, 154], "coord": [73, 28]},
    {"screen": [608, 433], "coord": [263, 193]},
]


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeDevice:
    def __init__(self, calls, clock):
        self.calls = calls
        self.clock = clock

    def click_adb(self, x, y):
        self.calls.append(("tap", x, y))

    def click_record_clear(self):
        self.calls.append(("click_record_clear",))

    def sleep(self, seconds):
        self.clock.advance(seconds)


class FakeGameUi:
    def __init__(self, positions, clock):
        self.calls = []
        self._positions = iter(positions)
        self._last_position = None
        self.clock = clock
        self.device = FakeDevice(self.calls, clock)
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def screenshot(self):
        return self.image

    def map_move_read_pos(self):
        try:
            self._last_position = next(self._positions)
        except StopIteration:
            pass
        return self._last_position

    def map_open_minimap_tap(self):
        self.calls.append(("open_minimap",))
        return True

    def map_pan_to_corner(self, corner):
        self.calls.append(("pan", corner))
        return True

    def map_close_minimap(self, timeout=8):
        self.calls.append(("close_minimap",))
        return True


def _navigator(positions, clock):
    host = FakeGameUi(positions, clock)
    navigator = MapCoordinateNavigator(
        host=host,
        map_database={"沼泽": {"views": {"top_left": SWAMP_SAMPLES}}},
        viewport_detector=lambda image: Viewport(161, 105, 756, 510, 1.0),
        clock=clock,
    )
    return host, navigator


def test_navigates_by_selected_view_and_returns_stable_actual_position():
    clock = FakeClock()
    host, navigator = _navigator(
        [("沼泽", 100, 100), ("沼泽", 296, 85), ("沼泽", 296, 85)], clock
    )

    result = navigator.go_to_coordinate(296, 85, stable_time=1, poll_interval=1)

    assert result == ("沼泽", 296, 85)
    assert host.calls.index(("pan", "top_left")) < host.calls.index(("tap", 664, 250))
    assert host.calls.index(("tap", 664, 250)) < host.calls.index(("close_minimap",))


def test_no_progress_logs_unreachable_and_clicks_once():
    clock = FakeClock()
    host, navigator = _navigator([("沼泽", 100, 100)], clock)

    with pytest.raises(TaskFailed, match="坐标不可达"):
        navigator.go_to_coordinate(
            296, 85, no_progress_timeout=2, poll_interval=1, stable_time=1
        )

    assert sum(call[0] == "tap" for call in host.calls) == 1


def test_target_outside_calibrated_views_fails_without_tapping():
    clock = FakeClock()
    host, navigator = _navigator([("沼泽", 100, 100)], clock)

    with pytest.raises(TaskFailed, match="视口未覆盖"):
        navigator.go_to_coordinate(900, 900)

    assert not any(call[0] == "tap" for call in host.calls)


def test_viewport_detection_failure_closes_minimap_and_fails():
    clock = FakeClock()
    host, navigator = _navigator([("沼泽", 100, 100)], clock)
    navigator.viewport_detector = lambda image: None

    with pytest.raises(TaskFailed, match="识别小地图视口"):
        navigator.go_to_coordinate(296, 85)

    assert ("close_minimap",) in host.calls
    assert not any(call[0] == "tap" for call in host.calls)


def test_missing_map_data_fails_before_opening_minimap():
    clock = FakeClock()
    host = FakeGameUi([("建木城外", 100, 100)], clock)
    navigator = MapCoordinateNavigator(
        host=host,
        map_database={},
        viewport_detector=lambda image: Viewport(161, 105, 756, 510, 1.0),
        clock=clock,
    )

    with pytest.raises(TaskFailed, match="没有坐标标定数据"):
        navigator.go_to_coordinate(296, 85)

    assert ("open_minimap",) not in host.calls


def test_stopping_outside_tolerance_fails_with_actual_position():
    clock = FakeClock()
    host, navigator = _navigator(
        [("沼泽", 100, 100), ("沼泽", 150, 100), ("沼泽", 150, 100)], clock
    )

    with pytest.raises(TaskFailed, match="实际停在 150,100"):
        navigator.go_to_coordinate(
            296, 85, no_progress_timeout=5, poll_interval=1, stable_time=2
        )

    assert sum(call[0] == "tap" for call in host.calls) == 1


def test_game_ui_facade_exposes_coordinate_navigation():
    from tasks.GameUi.game_ui import GameUi

    assert callable(getattr(GameUi, "map_go_to_coordinate", None))
