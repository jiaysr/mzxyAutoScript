from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median
from typing import Mapping, Sequence

import numpy as np


MIN_TOP_LEFT_SAMPLES = 4
MIN_COORD_SPAN = 20.0
MAX_FIT_RMSE = 2.5
VIEW_NAMES = ("top_left", "top_right", "bottom_left", "bottom_right")
MAPS_FILE = Path(__file__).with_name("maps.json")


class TargetNotCoveredError(ValueError):
    """No calibrated corner view can display the target pixel."""


@dataclass(frozen=True)
class MapSample:
    screen: tuple[float, float]
    coord: tuple[float, float]

    @classmethod
    def from_dict(cls, data: Mapping):
        screen = tuple(float(value) for value in data["screen"])
        coord = tuple(float(value) for value in data["coord"])
        if len(screen) != 2 or len(coord) != 2:
            raise ValueError("screen 和 coord 必须各含两个值")
        if not np.isfinite(screen).all() or not np.isfinite(coord).all():
            raise ValueError("screen 和 coord 必须是有限数值")
        return cls(screen=screen, coord=coord)


def load_map_database(path: str | Path = MAPS_FILE) -> dict:
    """Load and validate the user-maintained per-map, per-view samples."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    maps = payload.get("maps") if isinstance(payload, dict) else None
    if not isinstance(maps, dict):
        raise ValueError("地图数据 JSON 必须包含 maps 对象")
    for map_name, map_data in maps.items():
        if not isinstance(map_name, str) or not map_name.strip() or not isinstance(map_data, dict):
            raise ValueError("地图名和地图数据格式无效")
        views = map_data.get("views")
        if not isinstance(views, dict):
            raise ValueError(f"地图 {map_name} 缺少 views 对象")
        unknown_views = set(views) - set(VIEW_NAMES)
        if unknown_views:
            raise ValueError(f"地图 {map_name} 包含未知视口: {sorted(unknown_views)}")
        for view_name, samples in views.items():
            if not isinstance(samples, list):
                raise ValueError(f"地图 {map_name}/{view_name} 的样本必须是列表")
            for sample in samples:
                MapSample.from_dict(sample)
    return maps


@dataclass(frozen=True)
class ViewTransform:
    """Screen-pixel translation from the viewport origin for a panned view."""

    offset_x: float
    offset_y: float


@dataclass(frozen=True)
class MapCalibration:
    map_name: str
    scale_px_per_coord: tuple[float, float]
    views: dict[str, ViewTransform]

    @classmethod
    def fit(cls, map_name: str, samples_by_view: Mapping[str, Sequence[Mapping]], viewport):
        """Fit per-map pixel scales and per-corner screen offsets.

        The top-left viewport origin is the coordinate origin. Its scale is
        fitted through (0, 0); other corner offsets are inferred from samples.
        JSON samples are full-screen pixels, normalized by the detected viewport.
        """
        unknown = set(samples_by_view) - set(VIEW_NAMES)
        if unknown:
            raise ValueError(f"未知视口角落: {sorted(unknown)}")
        top_left_raw = list(samples_by_view.get("top_left") or ())
        if len(top_left_raw) < MIN_TOP_LEFT_SAMPLES:
            raise ValueError(f"左上视图样本不足: 至少需要 {MIN_TOP_LEFT_SAMPLES} 个")
        top_left = [MapSample.from_dict(item) for item in top_left_raw]

        px_x = np.asarray([sample.screen[0] - viewport.left for sample in top_left], dtype=float)
        px_y = np.asarray([sample.screen[1] - viewport.top for sample in top_left], dtype=float)
        coord_x = np.asarray([sample.coord[0] for sample in top_left], dtype=float)
        coord_y = np.asarray([sample.coord[1] for sample in top_left], dtype=float)
        scale_x = cls._fit_scale(px_x, coord_x, "X")
        scale_y = cls._fit_scale(px_y, coord_y, "Y")

        views = {"top_left": ViewTransform(0.0, 0.0)}
        cls._validate_residuals(top_left, viewport, (scale_x, scale_y), views["top_left"])
        for view_name in VIEW_NAMES[1:]:
            raw = list(samples_by_view.get(view_name) or ())
            if not raw:
                continue
            samples = [MapSample.from_dict(item) for item in raw]
            offsets_x = [sample.screen[0] - viewport.left - scale_x * sample.coord[0]
                         for sample in samples]
            offsets_y = [sample.screen[1] - viewport.top - scale_y * sample.coord[1]
                         for sample in samples]
            transform = ViewTransform(float(median(offsets_x)), float(median(offsets_y)))
            cls._validate_residuals(samples, viewport, (scale_x, scale_y), transform)
            views[view_name] = transform

        return cls(map_name=map_name,
                   scale_px_per_coord=(scale_x, scale_y),
                   views=views)

    @staticmethod
    def _fit_scale(pixel_offsets, coords, axis: str) -> float:
        span = float(coords.max() - coords.min())
        denom = float(np.dot(coords, coords))
        if span < MIN_COORD_SPAN or denom < 1e-9:
            raise ValueError(f"{axis} 坐标跨度不足: 至少需要 {MIN_COORD_SPAN:g} 格")
        scale = float(np.dot(coords, pixel_offsets) / denom)
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError(f"{axis} 轴标定比例无效")
        return scale

    @staticmethod
    def _validate_residuals(samples, viewport, scale, transform):
        errors_x = []
        errors_y = []
        for sample in samples:
            projected_x = ((sample.screen[0] - viewport.left - transform.offset_x) / scale[0])
            projected_y = ((sample.screen[1] - viewport.top - transform.offset_y) / scale[1])
            errors_x.append(projected_x - sample.coord[0])
            errors_y.append(projected_y - sample.coord[1])
        rmse_x = float(np.sqrt(np.mean(np.square(errors_x))))
        rmse_y = float(np.sqrt(np.mean(np.square(errors_y))))
        if max(rmse_x, rmse_y) > MAX_FIT_RMSE:
            raise ValueError(f"样本拟合残差过大: X={rmse_x:.2f}, Y={rmse_y:.2f} 格")

    def screen_for(self, view_name: str, coord: tuple[float, float], viewport):
        transform = self.views.get(view_name)
        if transform is None:
            raise ValueError(f"地图 {self.map_name} 未标定视口 {view_name}")
        return (
            viewport.left + self.scale_px_per_coord[0] * float(coord[0]) + transform.offset_x,
            viewport.top + self.scale_px_per_coord[1] * float(coord[1]) + transform.offset_y,
        )

    def select_view(self, coord: tuple[float, float], viewport, margin: int = 8):
        candidates = []
        for priority, view_name in enumerate(VIEW_NAMES):
            if view_name not in self.views:
                continue
            x, y = self.screen_for(view_name, coord, viewport)
            if not (viewport.left + margin <= x < viewport.right - margin
                    and viewport.top + margin <= y < viewport.bottom - margin):
                continue
            clearance = min(x - viewport.left, viewport.right - x,
                            y - viewport.top, viewport.bottom - y)
            candidates.append((clearance, -priority, view_name, (round(x), round(y))))
        if not candidates:
            raise TargetNotCoveredError(
                f"地图 {self.map_name} 的四角视口未覆盖目标 {tuple(coord)}")
        _, _, view_name, pixel = max(candidates)
        return view_name, pixel
