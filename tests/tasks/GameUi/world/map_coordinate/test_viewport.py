import numpy as np

from tasks.GameUi.world.map_coordinate.viewport import Viewport, detect_map_viewport


def _frame(left, top, width, height, divider, panel_right):
    image = np.full((720, 1280, 3), (62, 95, 45), dtype=np.uint8)
    border = np.array((0, 12, 24), dtype=np.uint8)
    outer_left = left - 3
    outer_top = top - 3
    outer_bottom = top + height + 3

    image[outer_top:top, outer_left:panel_right] = border
    image[top + height:outer_bottom, outer_left:panel_right] = border
    image[outer_top:outer_bottom, outer_left:left] = border
    image[outer_top:outer_bottom, divider:divider + 4] = border
    image[outer_top:outer_bottom, panel_right - 3:panel_right] = border
    image[top:top + height, divider + 4:panel_right - 3] = border
    return image


def test_detects_different_map_viewport_sizes_and_excludes_npc_panel():
    swamp = _frame(161, 105, 756, 510, 917, 1122)
    outland = _frame(262, 105, 554, 510, 816, 1022)

    swamp_view = detect_map_viewport(swamp)
    outland_view = detect_map_viewport(outland)

    assert isinstance(swamp_view, Viewport)
    assert swamp_view.rect == (161, 105, 756, 510)
    assert isinstance(outland_view, Viewport)
    assert outland_view.rect == (262, 105, 554, 510)
    assert outland_view.left + outland_view.width == 816


def test_returns_none_when_no_border_frame_exists():
    image = np.full((720, 1280, 3), (40, 80, 40), dtype=np.uint8)

    assert detect_map_viewport(image) is None
