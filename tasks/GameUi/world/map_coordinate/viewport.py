from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


# The color picker reports the border as packed 0x180C00 (BGR byte order).
# device.image is RGB, so the actual RGB color is (0, 12, 24).
MAP_BORDER_RGB = (0, 12, 24)
MAP_BORDER_TOLERANCE = 2
MIN_PANEL_SIZE = (700, 480)
MIN_VIEWPORT_SIZE = (400, 300)


@dataclass(frozen=True)
class Viewport:
    """Detected map-content viewport in 1280x720 screenshot coordinates."""

    left: int
    top: int
    width: int
    height: int
    confidence: float

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return inclusive ranges of consecutive true values."""
    indexes = np.flatnonzero(mask)
    if indexes.size == 0:
        return []
    groups = []
    start = previous = int(indexes[0])
    for index in indexes[1:]:
        index = int(index)
        if index > previous + 1:
            groups.append((start, previous))
            start = index
        previous = index
    groups.append((start, previous))
    return groups


def _panel_components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Find large, connected border-color components that could be a map popup."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    min_width, min_height = MIN_PANEL_SIZE
    components = []
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        if width >= min_width and height >= min_height and area >= 10000:
            components.append((x, y, width, height, area))
    return sorted(components, key=lambda item: item[4], reverse=True)


def _viewport_from_component(mask: np.ndarray,
                             component: tuple[int, int, int, int, int]
                             ) -> Optional[Viewport]:
    x, y, width, height, _ = component
    local = mask[y:y + height, x:x + width]
    row_support = local.sum(axis=1)
    col_support = local.sum(axis=0)

    # The popup has long horizontal top/bottom strokes and vertical left,
    # map/list divider, and right strokes. Require most of the full span so
    # isolated dark terrain pixels cannot become an edge.
    horizontal_runs = [
        run for run in _runs(row_support >= width * 0.72)
        if run[1] - run[0] + 1 >= 2
    ]
    vertical_runs = [
        run for run in _runs(col_support >= height * 0.72)
        if run[1] - run[0] + 1 >= 2
    ]
    top_runs = [run for run in horizontal_runs if run[0] <= height * 0.12]
    bottom_runs = [run for run in horizontal_runs if run[1] >= height * 0.88]
    left_runs = [run for run in vertical_runs if run[0] <= width * 0.08]
    if not top_runs or not bottom_runs or not left_runs:
        return None

    top_border = min(top_runs, key=lambda run: run[0])
    bottom_border = max(bottom_runs, key=lambda run: run[1])
    left_border = min(left_runs, key=lambda run: run[0])
    content_top = y + top_border[1] + 1
    content_bottom = y + bottom_border[0]
    content_left = x + left_border[1] + 1

    # A map/list split is a long vertical color run inside the outer popup,
    # normally 55%-90% of the popup width from its left edge.
    divider_candidates = []
    for start, end in vertical_runs:
        absolute_x = x + start
        relative_x = absolute_x - content_left
        if relative_x < MIN_VIEWPORT_SIZE[0]:
            continue
        if relative_x > width * 0.90:
            continue
        divider_candidates.append((absolute_x, start, end))
    if not divider_candidates:
        return None
    divider_x, divider_start, divider_end = min(divider_candidates, key=lambda item: item[0])

    view_width = divider_x - content_left
    view_height = content_bottom - content_top
    if view_width < MIN_VIEWPORT_SIZE[0] or view_height < MIN_VIEWPORT_SIZE[1]:
        return None

    top_strength = float(row_support[top_border[0]:top_border[1] + 1].max()) / width
    bottom_strength = float(row_support[bottom_border[0]:bottom_border[1] + 1].max()) / width
    left_strength = float(col_support[left_border[0]:left_border[1] + 1].max()) / height
    divider_strength = float(col_support[divider_start:divider_end + 1].max()) / height
    confidence = max(0.0, min(1.0, min(top_strength, bottom_strength,
                                       left_strength, divider_strength)))
    if confidence < 0.72:
        return None
    return Viewport(content_left, content_top, view_width, view_height, confidence)


def detect_map_viewport(image, tolerance: int = MAP_BORDER_TOLERANCE) -> Optional[Viewport]:
    """Locate the left map canvas, excluding the popup frame and NPC list.

    The border color picker value is BGR-packed ``0x180C00``; screenshots are
    RGB, so mask pixels close to ``(0, 12, 24)``. A missing or ambiguous frame
    returns ``None`` rather than guessing a click region.
    """
    if image is None or getattr(image, "ndim", 0) != 3 or image.shape[2] < 3:
        return None
    rgb = np.asarray(image[:, :, :3], dtype=np.int16)
    target = np.asarray(MAP_BORDER_RGB, dtype=np.int16)
    mask = np.max(np.abs(rgb - target), axis=2) <= max(0, int(tolerance))
    candidates = _panel_components(mask)
    if not candidates:
        return None

    viewports = []
    for component in candidates:
        viewport = _viewport_from_component(mask, component)
        if viewport is not None:
            viewports.append(viewport)
    if len(viewports) != 1:
        return None
    return viewports[0]
