import json

import pytest

from tasks.GameUi.world.map_coordinate.calibration import (
    MapCalibration,
    TargetNotCoveredError,
    ViewTransform,
    load_map_database,
)
from tasks.GameUi.world.map_coordinate.viewport import Viewport


SWAMP_TOP_LEFT_SAMPLES = [
    {"screen": [542, 208], "coord": [223, 61]},
    {"screen": [750, 239], "coord": [347, 79]},
    {"screen": [286, 154], "coord": [73, 28]},
    {"screen": [608, 433], "coord": [263, 193]},
]


def test_fits_swamp_top_left_samples_and_projects_target():
    raw = {"top_left": SWAMP_TOP_LEFT_SAMPLES}
    viewport = Viewport(161, 105, 756, 510, 1.0)

    calibration = MapCalibration.fit("沼泽", raw, viewport)

    assert calibration.scale_px_per_coord[0] == pytest.approx(1.70, abs=0.03)
    assert calibration.scale_px_per_coord[1] == pytest.approx(1.70, abs=0.03)
    assert calibration.screen_for("top_left", (296, 85), viewport) == pytest.approx((664, 249), abs=2)


def test_exact_sample_coordinate_reuses_original_click_pixel():
    calibration = MapCalibration.fit(
        "沼泽", {"top_left": SWAMP_TOP_LEFT_SAMPLES}, Viewport(161, 105, 756, 510, 1.0)
    )

    assert calibration.screen_for("top_left", (347, 79), Viewport(161, 105, 756, 510, 1.0)) == (750, 239)


def test_non_sample_target_rounds_to_best_neighboring_integer_pixel():
    calibration = MapCalibration(
        map_name="测试地图",
        scale_px_per_coord=(2.0, 2.0),
        views={"top_left": ViewTransform(0, 0)},
    )
    viewport = Viewport(100, 50, 300, 250, 1.0)

    assert calibration.screen_for("top_left", (1.3, 2.2), viewport) == (103, 54)


def test_fits_corner_offset_using_same_map_scale():
    viewport = Viewport(100, 50, 300, 250, 1.0)
    raw = {
        "top_left": [
            {"screen": [120, 70], "coord": [10, 10]},
            {"screen": [160, 70], "coord": [30, 10]},
            {"screen": [120, 110], "coord": [10, 30]},
            {"screen": [160, 110], "coord": [30, 30]},
        ],
        "top_right": [
            {"screen": [200, 70], "coord": [100, 10]},
            {"screen": [220, 110], "coord": [110, 30]},
        ],
    }

    calibration = MapCalibration.fit("测试地图", raw, viewport)

    assert calibration.scale_px_per_coord == pytest.approx((2.0, 2.0))
    assert calibration.views["top_right"] == ViewTransform(-100, 0)
    assert calibration.screen_for("top_right", (150, 20), viewport) == pytest.approx((300, 90))


def test_view_with_too_few_samples_is_not_usable():
    raw = {"top_left": [{"screen": [500, 200], "coord": [100, 50]}]}
    viewport = Viewport(160, 105, 750, 510, 1.0)

    with pytest.raises(ValueError, match="样本不足"):
        MapCalibration.fit("沼泽", raw, viewport)


def test_fits_single_axis_two_corner_map():
    viewport = Viewport(161, 105, 756, 510, 1.0)
    raw = {
        "top_left": [
            {"screen": [200, 150], "coord": [20, 20]},
            {"screen": [400, 150], "coord": [140, 20]},
            {"screen": [200, 300], "coord": [20, 110]},
            {"screen": [400, 300], "coord": [140, 110]},
        ],
        "bottom_left": [
            {"screen": [200, 500], "coord": [20, 230]},
        ],
    }

    calibration = MapCalibration.fit("纵向单轴地图", raw, viewport)

    assert calibration.estimated is False
    assert set(calibration.views) == {"top_left", "bottom_left"}
    assert calibration.screen_for("bottom_left", (20, 230), viewport) == (200, 500)


def test_fits_fixed_small_map_with_only_one_view():
    viewport = Viewport(161, 105, 756, 510, 1.0)
    raw = {
        "top_left": [
            {"screen": [200, 150], "coord": [20, 20]},
            {"screen": [400, 150], "coord": [140, 20]},
            {"screen": [200, 300], "coord": [20, 110]},
            {"screen": [400, 300], "coord": [140, 110]},
        ],
    }

    calibration = MapCalibration.fit("小地图", raw, viewport)

    assert set(calibration.views) == {"top_left"}
    assert calibration.select_view((80, 60), viewport)[0] == "top_left"


def test_fits_scale_from_the_only_calibrated_corner():
    viewport = Viewport(200, 100, 600, 400, 1.0)
    raw = {
        "top_right": [
            {"screen": [500, 200], "coord": [100, 50]},
            {"screen": [700, 200], "coord": [220, 50]},
            {"screen": [500, 400], "coord": [100, 170]},
            {"screen": [700, 400], "coord": [220, 170]},
        ],
        "bottom_left": [
            {"screen": [300, 500], "coord": [30, 230]},
        ],
    }

    calibration = MapCalibration.fit("右上+左下", raw, viewport)

    assert calibration.scale_px_per_coord == pytest.approx((200 / 120, 200 / 120))
    assert set(calibration.views) == {"top_right", "bottom_left"}
    assert calibration.screen_for("bottom_left", (30, 230), viewport) == (300, 500)


def test_view_with_high_residual_is_not_usable():
    raw = {
        "top_left": [
            {"screen": [260, 205], "coord": [60, 60]},
            {"screen": [460, 205], "coord": [180, 60]},
            {"screen": [260, 405], "coord": [60, 180]},
            {"screen": [460, 405], "coord": [10, 10]},
        ]
    }
    viewport = Viewport(160, 105, 750, 510, 1.0)

    with pytest.raises(ValueError, match="残差"):
        MapCalibration.fit("沼泽", raw, viewport)


def _four_corner_calibration(scale=(1.0, 1.0)):
    return MapCalibration(
        map_name="合成测试地图",
        scale_px_per_coord=scale,
        views={
            "top_left": ViewTransform(0, 0),
            "top_right": ViewTransform(-100, 0),
            "bottom_left": ViewTransform(0, -100),
            "bottom_right": ViewTransform(-100, -100),
        },
    )


def test_selects_view_that_contains_target():
    calibration = _four_corner_calibration()
    viewport = Viewport(200, 100, 300, 250, 1.0)

    view, pixel = calibration.select_view((350, 300), viewport, margin=8)

    assert view == "bottom_right"
    assert pixel == pytest.approx((450, 300))


def test_target_outside_all_views_is_rejected():
    calibration = _four_corner_calibration()
    viewport = Viewport(200, 100, 300, 250, 1.0)

    with pytest.raises(TargetNotCoveredError, match="未覆盖"):
        calibration.select_view((900, 900), viewport, margin=8)


def test_estimate_uses_viewport_origin_as_map_zero():
    calibration = MapCalibration.estimate("建木城外")
    viewport = Viewport(161, 105, 756, 510, 1.0)

    assert calibration.estimated is True
    assert calibration.views == {"top_left": ViewTransform(0, 0)}
    assert calibration.scale_px_per_coord == pytest.approx((1.69, 1.69))
    assert calibration.screen_for("top_left", (296, 85), viewport) == (661, 249)


def test_estimate_rejects_target_outside_top_left_view():
    calibration = MapCalibration.estimate("建木城外")
    viewport = Viewport(161, 105, 756, 510, 1.0)

    with pytest.raises(TargetNotCoveredError, match="未覆盖"):
        calibration.select_view((900, 900), viewport)


def test_estimate_rejects_invalid_scale():
    with pytest.raises(ValueError, match="正数"):
        MapCalibration.estimate("建木城外", scale=(0, 1.69))


def test_load_map_database_returns_named_absolute_samples(tmp_path):
    payload = {
        "version": 1,
        "maps": {
            "沼泽": {"views": {"top_left": SWAMP_TOP_LEFT_SAMPLES}},
        },
    }
    path = tmp_path / "maps.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    maps = load_map_database(path)

    assert maps["沼泽"]["views"]["top_left"] == SWAMP_TOP_LEFT_SAMPLES


def test_load_map_database_rejects_unknown_schema(tmp_path):
    path = tmp_path / "maps.json"
    path.write_text('{"version": 1}', encoding="utf-8")

    with pytest.raises(ValueError, match="maps"):
        load_map_database(path)


def test_bundled_map_samples_fit_known_top_left_viewports():
    maps = load_map_database()
    swamp = MapCalibration.fit(
        "沼泽", maps["沼泽"]["views"], Viewport(161, 105, 756, 510, 1.0)
    )
    outland = MapCalibration.fit(
        "域外灵岛", maps["域外灵岛"]["views"], Viewport(220, 105, 637, 510, 1.0)
    )

    assert swamp.scale_px_per_coord == pytest.approx((1.70, 1.70), abs=0.03)
    assert outland.scale_px_per_coord == pytest.approx((1.70, 1.70), abs=0.03)
