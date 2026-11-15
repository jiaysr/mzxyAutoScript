import numpy as np
import pytest

from tasks.GameUi.world.map_coordinate.recorder import MapSampleRecorder
from tasks.GameUi.world.map_coordinate.viewport import Viewport


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeDevice:
    def __init__(self, host):
        self.host = host

    def click_adb(self, x, y):
        self.host.calls.append(("tap", x, y))

    def click_record_clear(self):
        self.host.calls.append(("clear",))

    def sleep(self, seconds):
        self.host.clock.advance(seconds)


class FakeHost:
    def __init__(self, positions, clock):
        self.calls = []
        self.clock = clock
        self._positions = iter(positions)
        self._last = None
        self.device = FakeDevice(self)
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.ensure_main = True
        self.open_ok = True
        self.pan_ok = True
        self.close_ok = True

    def map_move_ensure_main_page(self):
        self.calls.append(("main",))
        return self.ensure_main

    def map_move_read_pos(self):
        try:
            self._last = next(self._positions)
        except StopIteration:
            pass
        self.calls.append(("read",))
        return self._last

    def map_open_minimap_tap(self):
        self.calls.append(("open",))
        return self.open_ok

    def map_pan_to_corner(self, corner):
        self.calls.append(("pan", corner))
        return self.pan_ok

    def map_close_minimap(self, timeout=8):
        self.calls.append(("close",))
        return self.close_ok

    def screenshot(self):
        self.calls.append(("screenshot",))
        return self.image


def _recorder(host, plan, clock, **options):
    settings = dict(stable_time=1, timeout=30, no_progress_timeout=5, poll_interval=1)
    settings.update(options)
    return MapSampleRecorder(
        host=host,
        plan=plan,
        viewport_detector=lambda image: Viewport(161, 105, 756, 510, 1.0),
        clock=clock,
        **settings,
    )


def test_records_samples_in_plan_order_with_map_name_and_coords():
    clock = FakeClock()
    host = FakeHost([
        ("沼泽", 100, 100),   # 初始位置
        ("沼泽", 120, 100),   # 第一个点：出发
        ("沼泽", 223, 61),    # 第一个点：到达
        ("沼泽", 223, 61),    # 第一个点：稳定
        ("沼泽", 230, 70),    # 第二个点：出发
        ("沼泽", 263, 193),   # 第二个点：到达
        ("沼泽", 263, 193),   # 第二个点：稳定
        ("沼泽", 280, 210),   # 第三个点：出发
        ("沼泽", 430, 70),    # 第三个点：到达
        ("沼泽", 430, 70),    # 第三个点：稳定
    ], clock)

    result = _recorder(host, {
        "top_left": [(542, 208), (750, 239)],
        "top_right": [(900, 220)],
    }, clock).record()

    assert result.map_name == "沼泽"
    assert result.sample_count == 3
    assert result.views == {
        "top_left": [
            {"screen": [542, 208], "coord": [223, 61]},
            {"screen": [750, 239], "coord": [263, 193]},
        ],
        "top_right": [
            {"screen": [900, 220], "coord": [430, 70]},
        ],
    }
    assert result.failed == []
    assert result.fatal_error is None
    assert result.plan == {
        "top_left": [(542, 208), (750, 239)],
        "top_right": [(900, 220)],
    }
    assert [call for call in host.calls if call[0] == "pan"] == [
        ("pan", "top_left"), ("pan", "top_left"), ("pan", "top_right"),
    ]
    assert [call for call in host.calls if call[0] == "tap"] == [
        ("tap", 542, 208), ("tap", 750, 239), ("tap", 900, 220),
    ]


def test_click_without_movement_is_reported_as_failed():
    clock = FakeClock()
    host = FakeHost([
        ("沼泽", 100, 100),
        ("沼泽", 100, 100),
        ("沼泽", 100, 100),
        ("沼泽", 100, 100),
    ], clock)

    result = _recorder(
        host, {"top_left": [(542, 208)]}, clock, no_progress_timeout=2,
    ).record()

    assert result.views == {}
    assert len(result.failed) == 1
    assert "未移动" in result.failed[0]["error"]
    assert [call for call in host.calls if call[0] == "tap"] == [("tap", 542, 208)]


def test_click_outside_detected_viewport_is_skipped():
    clock = FakeClock()
    host = FakeHost([("沼泽", 100, 100)], clock)

    result = _recorder(host, {"top_left": [(1200, 650)]}, clock).record()

    assert result.views == {}
    assert len(result.failed) == 1
    assert "视口安全区" in result.failed[0]["error"]
    assert not any(call[0] == "tap" for call in host.calls)


def test_map_change_stops_remaining_samples():
    clock = FakeClock()
    host = FakeHost([
        ("沼泽", 100, 100),
        ("沼泽", 120, 100),
        ("建木城外", 50, 50),
        ("建木城外", 50, 50),
    ], clock)

    result = _recorder(host, {
        "top_left": [(542, 208)],
        "top_right": [(900, 220)],
    }, clock).record()

    assert result.views == {}
    assert result.fatal_error is not None
    assert "地图在录制期间改变" in result.fatal_error
    assert sum(call[0] == "tap" for call in host.calls) == 1
    assert ("pan", "top_right") not in host.calls


def test_normalize_plan_accepts_views_wrapper_and_screen_objects():
    plan = {
        "views": {
            "top_left": [{"screen": [542.4, 208.6]}],
            "bottom_right": [[900, 220]],
        }
    }

    assert MapSampleRecorder.normalize_plan(plan) == {
        "top_left": [(542, 209)],
        "bottom_right": [(900, 220)],
    }


def test_normalize_plan_rejects_unknown_view():
    with pytest.raises(ValueError, match="未知视口方向"):
        MapSampleRecorder.normalize_plan({"center": [(100, 100)]})
