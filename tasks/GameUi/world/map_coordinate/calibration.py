from dataclasses import dataclass, field
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
    samples: dict[str, tuple[MapSample, ...]] = field(default_factory=dict)

    @classmethod
    def fit(cls, map_name: str, samples_by_view: Mapping[str, Sequence[Mapping]], viewport):
        """Fit per-map pixel scales and per-corner screen offsets.

        JSON samples are full-screen pixels, normalized by the detected viewport.
        The per-map scale and all view intercepts are fitted from samples; no
        pixel origin is entered by the user.
        """
        unknown = set(samples_by_view) - set(VIEW_NAMES)
        if unknown:
            raise ValueError(f"未知视口角落: {sorted(unknown)}")
        top_left_raw = list(samples_by_view.get("top_left") or ())
        if len(top_left_raw) < MIN_TOP_LEFT_SAMPLES:
            raise ValueError(f"左上视图样本不足: 至少需要 {MIN_TOP_LEFT_SAMPLES} 个")
        top_left = [MapSample.from_dict(item) for item in top_left_raw]

        normalized = {
            view_name: tuple(
                MapSample(
                    screen=(sample.screen[0] - viewport.left,
                            sample.screen[1] - viewport.top),
                    coord=sample.coord,
                )
                for sample in (MapSample.from_dict(item) for item in raw_samples)
            )
            for view_name, raw_samples in samples_by_view.items()
            if raw_samples
        }
        top_left = list(normalized["top_left"])
        px_x = np.asarray([sample.screen[0] for sample in top_left], dtype=float)
        px_y = np.asarray([sample.screen[1] for sample in top_left], dtype=float)
        coord_x = np.asarray([sample.coord[0] for sample in top_left], dtype=float)
        coord_y = np.asarray([sample.coord[1] for sample in top_left], dtype=float)
        scale_x, offset_top_left_x = cls._fit_line(coord_x, px_x, "X")
        scale_y, offset_top_left_y = cls._fit_line(coord_y, px_y, "Y")

        views = {"top_left": ViewTransform(offset_top_left_x, offset_top_left_y)}
        cls._validate_residuals(top_left, (scale_x, scale_y), views["top_left"])
        for view_name in VIEW_NAMES[1:]:
            samples = list(normalized.get(view_name) or ())
            if not samples:
                continue
            offsets_x = [sample.screen[0] - scale_x * sample.coord[0]
                         for sample in samples]
            offsets_y = [sample.screen[1] - scale_y * sample.coord[1]
                         for sample in samples]
            transform = ViewTransform(float(median(offsets_x)), float(median(offsets_y)))
            cls._validate_residuals(samples, (scale_x, scale_y), transform)
            views[view_name] = transform

        return cls(map_name=map_name,
                   scale_px_per_coord=(scale_x, scale_y),
                   views=views,
                   samples=normalized)

    @staticmethod
    def _fit_line(coords, pixels, axis: str) -> tuple[float, float]:
        span = float(coords.max() - coords.min())
        centered_coords = coords - float(coords.mean())
        centered_pixels = pixels - float(pixels.mean())
        denom = float(np.dot(centered_coords, centered_coords))
        if span < MIN_COORD_SPAN or denom < 1e-9:
            raise ValueError(f"{axis} 坐标跨度不足: 至少需要 {MIN_COORD_SPAN:g} 格")
        scale = float(np.dot(centered_coords, centered_pixels) / denom)
        offset = float(pixels.mean() - scale * coords.mean())
        if not np.isfinite(scale) or scale <= 0 or not np.isfinite(offset):
            raise ValueError(f"{axis} 轴标定比例无效")
        return scale, offset

    @staticmethod
    def _validate_residuals(samples, scale, transform):
        errors_x = []
        errors_y = []
        for sample in samples:
            projected_x = ((sample.screen[0] - transform.offset_x) / scale[0])
            projected_y = ((sample.screen[1] - transform.offset_y) / scale[1])
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

        # Preserve a user's exact successful sample. Integer tile boundaries
        # can make a one-pixel rounding choice select a neighboring map cell.
        for sample in self.samples.get(view_name, ()):
            if (abs(sample.coord[0] - float(coord[0])) < 1e-9
                    and abs(sample.coord[1] - float(coord[1])) < 1e-9):
                return (round(viewport.left + sample.screen[0]),
                        round(viewport.top + sample.screen[1]))

        ideal_x = self.scale_px_per_coord[0] * float(coord[0]) + transform.offset_x
        ideal_y = self.scale_px_per_coord[1] * float(coord[1]) + transform.offset_y
        x_candidates = range(int(np.floor(ideal_x)) - 1, int(np.ceil(ideal_x)) + 2)
        y_candidates = range(int(np.floor(ideal_y)) - 1, int(np.ceil(ideal_y)) + 2)
        candidates = []
        for local_x in x_candidates:
            for local_y in y_candidates:
                projected_x = (local_x - transform.offset_x) / self.scale_px_per_coord[0]
                projected_y = (local_y - transform.offset_y) / self.scale_px_per_coord[1]
                world_error = ((projected_x - coord[0]) ** 2
                               + (projected_y - coord[1]) ** 2)
                pixel_error = ((local_x - ideal_x) ** 2 + (local_y - ideal_y) ** 2)
                candidates.append((world_error, pixel_error, local_x, local_y))
        _, _, local_x, local_y = min(candidates)
        return viewport.left + local_x, viewport.top + local_y

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
