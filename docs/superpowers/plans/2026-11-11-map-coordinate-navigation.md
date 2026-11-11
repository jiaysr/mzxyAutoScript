# 小地图坐标寻路模块实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立模块中自动识别小地图视口，按地图名和四角可达样本，将用户地图坐标转换为点击位置并寻路；不可达时抛业务失败。

**Architecture:** 新建 `tasks/GameUi/world/map_coordinate/` 包，拆分视口检测、地图样本拟合、四角视图选择与设备寻路。`GameUi` 通过 mixin 暴露 `map_go_to_coordinate()`；每图数据放 `maps.json`，由自动检测到的视口左上角归一化绝对样本像素。

**Tech Stack:** Python 3.10、NumPy/OpenCV、Pydantic/JSON、pytest、现有 `GameUi` 与 ADB/minitouch 设备控制。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `tasks/GameUi/world/map_coordinate/__init__.py` | 对外导出 `MapCoordinateNavigator`、`MapCoordinateNavigation` 与数据类型 |
| `tasks/GameUi/world/map_coordinate/viewport.py` | 用边框色检测地图内容矩形、内部分隔线和置信度 |
| `tasks/GameUi/world/map_coordinate/calibration.py` | 读取每图/每角样本，拟合坐标变换，选择覆盖目标的视图 |
| `tasks/GameUi/world/map_coordinate/navigator.py` | 打开/平移/点图/等待，成功返回位置，不可达抛 `TaskFailed` |
| `tasks/GameUi/world/map_coordinate/maps.json` | 当前已知地图的手工样本；未录入的角视图保持未标定 |
| `tasks/GameUi/world/map_coordinate/README.md` | 说明如何为新地图录入四角样本及确认可达性 |
| `tasks/GameUi/game_ui.py` | 将新 mixin 接入 `GameUi` 门面 |
| `tests/tasks/GameUi/world/map_coordinate/test_viewport.py` | 颜色边框及不同尺寸视口检测 |
| `tests/tasks/GameUi/world/map_coordinate/test_calibration.py` | 样本拟合、四角偏移、覆盖选择、缺失/异常数据 |
| `tests/tasks/GameUi/world/map_coordinate/test_navigator.py` | 寻路成功、不可达、超时和失败流程 |

不修改 `tasks/GameUi/world/map.py` 的公共传送行为，不修改已有 `tasks/DailySignIn/script_task.py` 工作区改动。

## Task 1: 颜色边框自动检测视口

**Files:**
- Create: `tasks/GameUi/world/map_coordinate/__init__.py`
- Create: `tasks/GameUi/world/map_coordinate/viewport.py`
- Test: `tests/tasks/GameUi/world/map_coordinate/test_viewport.py`

- [x] **Step 1: 写合成截图的失败测试**

建立包含地图纹理、深色右侧 NPC 栏、外框和地图/NPC 分隔线的 1280×720 RGB 图片。测试两个视口尺寸，并确认返回的是左侧地图画布，而非整个弹窗。

```python
import numpy as np

from tasks.GameUi.world.map_coordinate.viewport import detect_map_viewport


def _frame(left, top, width, height, divider, panel_right):
    image = np.full((720, 1280, 3), (62, 95, 45), dtype=np.uint8)
    border = np.array((0, 12, 24), dtype=np.uint8)  # 取色器 0x180C00 的 RGB 字节序
    x0, y0 = left - 3, top - 3
    x1, y1 = divider + 4, top + height + 3
    image[y0:y0 + 3, x0:panel_right] = border
    image[y1 - 3:y1, x0:panel_right] = border
    image[y0:y1, x0:x0 + 3] = border
    image[y0:y1, divider:divider + 4] = border
    image[y0:y1, panel_right - 3:panel_right] = border
    image[top:top + height, divider + 4:panel_right - 3] = (0, 12, 24)
    return image


def test_detects_different_map_viewport_sizes():
    swamp = _frame(161, 105, 756, 510, 917, 1122)
    island = _frame(262, 105, 554, 510, 816, 1022)

    assert detect_map_viewport(swamp).rect == (161, 105, 756, 510)
    assert detect_map_viewport(island).rect == (262, 105, 554, 510)


def test_returns_none_when_frame_or_divider_is_ambiguous():
    image = np.full((720, 1280, 3), (40, 80, 40), dtype=np.uint8)
    assert detect_map_viewport(image) is None
```

- [x] **Step 2: 运行测试确认失败**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_viewport.py -q`
预期：因 `viewport.py` 尚不存在而失败。

- [x] **Step 3: 实现最小视口检测**

在 `viewport.py` 定义不可变 `Viewport(left, top, width, height, confidence)`。将 RGB 图中与 `(0,12,24)` 通道最大差不超过 2 的像素建掩码；在地图弹窗区域找最大长方形边框及其第一个长竖向分隔线；地图区取左边框内沿至分隔线内沿、上/下边框内沿。缺少闭合边线、地图区小于 `400×300` 或候选不唯一时返回 `None`。

- [x] **Step 4: 运行测试确认通过**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_viewport.py -q`
预期：两个不同尺寸视口均返回各自的矩形，纯背景帧返回 `None`。

- [x] **Step 5: 提交该任务**

只暂存 viewport 文件和对应测试，提交信息：`feat: detect minimap viewport from border color`。

## Task 2: 每图四角样本与坐标变换

**Files:**
- Create: `tasks/GameUi/world/map_coordinate/calibration.py`
- Create: `tasks/GameUi/world/map_coordinate/maps.json`
- Test: `tests/tasks/GameUi/world/map_coordinate/test_calibration.py`

- [x] **Step 1: 写样本拟合测试**

样本格式为用户输入的整屏像素与到达坐标；运行时从样本屏幕坐标中扣除自动检测到的视口左上角。每图拟合带截距的 X/Y 比例，截距由样本自动求出而非强制为零；其他角视图在共享比例上拟合各自偏移。精确命中已有样本坐标时必须复用原始点击像素；其他目标则评估连续反算点邻域内的整数像素，选前向回投坐标误差最小者。

```python
import pytest

from tasks.GameUi.world.map_coordinate.calibration import MapCalibration
from tasks.GameUi.world.map_coordinate.viewport import Viewport


def test_fits_swamp_top_left_samples_and_projects_target():
    raw = {
        "top_left": [
            {"screen": [542, 208], "coord": [223, 61]},
            {"screen": [750, 239], "coord": [347, 79]},
            {"screen": [286, 154], "coord": [73, 28]},
            {"screen": [608, 433], "coord": [263, 193]},
        ]
    }
    viewport = Viewport(161, 105, 756, 510, 1.0)
    calibration = MapCalibration.fit("沼泽", raw, viewport)

    assert calibration.scale_px_per_coord[0] == pytest.approx(1.70, abs=0.03)
    assert calibration.scale_px_per_coord[1] == pytest.approx(1.70, abs=0.03)
    assert calibration.screen_for("top_left", (347, 79), viewport) == (750, 239)
    assert calibration.screen_for("top_left", (296, 85), viewport) == pytest.approx((664, 250), abs=2)


def test_neighbor_pixel_rounding_minimizes_forward_coordinate_error():
    calibration = MapCalibration(
        map_name="测试地图",
        scale_px_per_coord=(2.0, 2.0),
        views={"top_left": ViewTransform(0, 0)},
    )
    viewport = Viewport(100, 50, 300, 250, 1.0)
    assert calibration.screen_for("top_left", (1.3, 2.2), viewport) == (103, 54)


def test_view_with_too_few_samples_is_not_usable():
    raw = {"top_left": [{"screen": [500, 200], "coord": [100, 50]}]}
    viewport = Viewport(160, 105, 750, 510, 1.0)
    with pytest.raises(ValueError, match="样本不足"):
        MapCalibration.fit("沼泽", raw, viewport)


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
```

- [x] **Step 2: 运行测试确认失败**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_calibration.py -q`
预期：因 `calibration.py` 尚不存在而失败。

- [x] **Step 3: 实现数据加载与拟合**

定义 `MapSample(screen, coord)`、`ViewTransform(offset_x, offset_y)` 和 `MapCalibration(map_name, scale_px_per_coord, views, samples)`。`ViewTransform` 的偏移单位为相对视口左上角的屏幕像素。左上视图至少 4 个样本，按每轴 `pixel_offset = pixels_per_coord × map_coord + intercept` 进行最小二乘拟合；截距自动由样本求出。右上/左下/右下使用同一比例，并由各自样本拟合偏移。保留归一化样本以便精确命中时复用原始点击像素；非样本目标在连续反算点附近评估整数像素候选，选坐标前向回投误差最小者。对每个样本计算回投残差，均方根超过 2.5 格或任一方向坐标跨度不足 20 格时拒绝该标定。

- [x] **Step 4: 填入已有左上视图样本**

在 `maps.json` 加入下列已验证样本；其他角视图暂不写入，读取时视作未标定，不以空数组伪造覆盖：

```json
{
  "version": 1,
  "maps": {
    "沼泽": {
      "views": {
        "top_left": [
          {"screen": [542, 208], "coord": [223, 61]},
          {"screen": [750, 239], "coord": [347, 79]},
          {"screen": [286, 154], "coord": [73, 28]},
          {"screen": [608, 433], "coord": [263, 193]}
        ]
      }
    },
    "域外灵岛": {
      "views": {
        "top_left": [
          {"screen": [591, 310], "coord": [219, 120]},
          {"screen": [457, 316], "coord": [140, 123]},
          {"screen": [363, 506], "coord": [82, 235]},
          {"screen": [552, 593], "coord": [193, 287]}
        ]
      }
    }
  }
}
```

- [x] **Step 5: 补四角偏移和失败测试**

用合成的四角校准样本验证同一目标在四视图的投影；测试缺少角视图时 `screen_for()` 抛 `ValueError`，不回退到左上或其他地图参数。

- [x] **Step 6: 运行测试并提交**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_calibration.py -q`
预期：样本拟合、投影、缺数据及残差拒绝用例通过。只暂存 calibration、maps.json 和测试。

## Task 3: 四角视图选择

**Files:**
- Modify: `tasks/GameUi/world/map_coordinate/calibration.py`
- Test: `tests/tasks/GameUi/world/map_coordinate/test_calibration.py`

- [x] **Step 1: 写视图选择失败测试**

给定每角已标定变换与 `Viewport`，将目标投影到各视图；选择投影位于视口安全边距内且离边缘最远的视图。没有任何视图覆盖目标时明确返回“未覆盖”，不点屏。

```python
import pytest

from tasks.GameUi.world.map_coordinate.calibration import (
    MapCalibration,
    TargetNotCoveredError,
    ViewTransform,
)
from tasks.GameUi.world.map_coordinate.viewport import Viewport


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
```

- [x] **Step 2: 运行测试确认失败**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_calibration.py -q`
预期：因 `select_view()` 尚未实现而失败。

- [x] **Step 3: 实现四角视图选择**

遍历已标定视图，反算目标像素；剔除在视口外或距边缘小于 8 px 的候选，按到最近边缘的最小距离降序选择。所有角均不覆盖时返回专门的 `TargetNotCoveredError`。

- [x] **Step 4: 运行测试并提交**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_calibration.py -q`
预期：选中 bottom-right 的例子通过，覆盖缺口抛 `TargetNotCoveredError`。

## Task 4: 点击寻路与任务失败集成

**Files:**
- Create: `tasks/GameUi/world/map_coordinate/navigator.py`
- Modify: `tasks/GameUi/world/map_coordinate/__init__.py`
- Modify: `tasks/GameUi/game_ui.py`
- Test: `tests/tasks/GameUi/world/map_coordinate/test_navigator.py`

- [x] **Step 1: 写成功路径编排测试**

使用假 GameUi 记录调用顺序：读取地图名与起点、打开小地图、识别视口、平移到所选角、点击一次、关闭地图、读回目标坐标。成功时返回真实到达坐标。

```python
import time

import numpy as np

from tasks.GameUi.world.map_coordinate.calibration import MapCalibration, ViewTransform
from tasks.GameUi.world.map_coordinate.navigator import MapCoordinateNavigator
from tasks.GameUi.world.map_coordinate.viewport import Viewport
from module.exception import TaskFailed


class FakeDevice:
    def __init__(self, calls):
        self.calls = calls

    def click_adb(self, x, y):
        self.calls.append(("tap", x, y))

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))
        time.sleep(min(seconds, 0.01))


class FakeGameUi:
    def __init__(self, positions, image):
        self.calls = []
        self._positions = iter(positions)
        self._last_position = None
        self.device = FakeDevice(self.calls)
        self.image = image

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


def calibration_with_top_left():
    return MapCalibration(
        map_name="沼泽",
        scale_px_per_coord=(1.70, 1.695),
        views={"top_left": ViewTransform(0, 0)},
    )


def test_map_go_to_coordinate_clicks_selected_view_and_returns_actual():
    game = FakeGameUi(
        positions=[
            ("沼泽", 100, 100),
            ("沼泽", 296, 85),
            ("沼泽", 296, 85),
            ("沼泽", 296, 85),
        ],
        image=np.zeros((720, 1280, 3), dtype=np.uint8),
    )
    navigator = MapCoordinateNavigator(
        host=game,
        calibrations={"沼泽": calibration_with_top_left()},
        viewport_detector=lambda image: Viewport(161, 105, 756, 510, 1.0),
    )
    result = navigator.go_to_coordinate(296, 85, stable_time=0.02, poll_interval=0.01)
    assert result == ("沼泽", 296, 85)
    assert game.calls.index(("pan", "top_left")) < game.calls.index(("tap", 664, 249))
    assert game.calls.index(("tap", 664, 249)) < game.calls.index(("close_minimap",))
```

- [x] **Step 2: 写不可达与缺数据失败测试**

验证点击后坐标 5 秒不变时记录 `坐标不可达` 并抛 `TaskFailed`；稳定停在目标容差外 3 秒、缺地图数据、视口检测失败和四角覆盖缺失也抛 `TaskFailed`。校验这些失败路径没有二次点击。

```python
import pytest

from module.exception import TaskFailed


def test_no_progress_logs_unreachable_and_clicks_only_once():
    game = FakeGameUi(
        positions=[("沼泽", 100, 100)],  # 耗尽后 FakeGameUi 保持最后位置不变
        image=np.zeros((720, 1280, 3), dtype=np.uint8),
    )
    navigator = MapCoordinateNavigator(
        host=game,
        calibrations={"沼泽": calibration_with_top_left()},
        viewport_detector=lambda image: Viewport(161, 105, 756, 510, 1.0),
    )

    with pytest.raises(TaskFailed, match="坐标不可达"):
        navigator.go_to_coordinate(
            296, 85, no_progress_timeout=0.02, poll_interval=0.01, stable_time=0.02
        )

    assert sum(call[0] == "tap" for call in game.calls) == 1
```

`calibration_with_top_left()` 在同一测试文件中使用 `MapCalibration`、`scale_px_per_coord=(1.70,1.695)` 和 `ViewTransform(0,0)` 构造沼泽左上视图；此 helper 与成功测试使用同一份定义。

- [x] **Step 3: 运行测试确认失败**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_navigator.py -q`
预期：因导航 mixin 尚未实现而失败。

- [x] **Step 4: 实现 MapCoordinateNavigation**

实现 `map_go_to_coordinate(x, y, map_name=None, timeout=60, tolerance=1, no_progress_timeout=5, stable_time=3)`。通过 `map_move_read_pos()` 获取地图名；当前位于小地图时先关闭以读取坐标。打开小地图后检测视口并调用 `select_view()`，用 `device.click_adb()` 点一次。关闭小地图后每秒读位置；达到每轴误差容差返回实际位置，停滞/稳定偏差/超时写明目标与实际坐标并抛 `TaskFailed`。缺数据、检测置信度不足、目标未覆盖也抛 `TaskFailed`。不处理/确认 NPC 对话。

- [x] **Step 5: 接入 GameUi 门面**

在 `game_ui.py` 导入 `MapCoordinateNavigation` 并加入 `GameUi` mixin 列表，保证 `ScriptTask` 可直接调用 `self.map_go_to_coordinate(...)`。不改动调用方任务，不修改 `DailySignIn` 文件。

- [x] **Step 6: 运行测试并提交**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate/test_navigator.py -q`
预期：成功路径返回实坐标；所有业务失败路径均抛 `TaskFailed` 且不重试点击。只暂存新包、GameUi 门面和对应测试。

## Task 5: 数据录入说明与回归验证

**Files:**
- Create: `tasks/GameUi/world/map_coordinate/README.md`
- Test: `tests/tasks/GameUi/world/map_coordinate/`

- [x] **Step 1: 编写 JSON 录入说明**

说明每条 `screen` 是 1280×720 整屏绝对点击像素、`coord` 是到达后稳定读取的真实坐标；样本必须对应地图名和四角视图状态，并且点击点可达。给出一个完整 top-left 示例和四角扩展示例。强调未配置视图会安全失败。

- [x] **Step 2: 运行新模块完整测试**

运行：`toolkit\python.exe -m pytest tests/tasks/GameUi/world/map_coordinate -q`
预期：视口、标定、四角选择、寻路编排所有用例通过。

- [x] **Step 3: 运行地图与 GameUi 回归测试**

运行：`toolkit\python.exe -m pytest tests/tasks/Dungeon/test_boss_reward_popup.py tests/tasks/LimitedTime/test_wild_snowman_script.py -q`
预期：既有地图平移、地图寻路调用方不受影响。

- [ ] **Step 4: 真机手动验收**

只在有完整四角数据的地图上进行。记录起点，目标分别选一个左上视图和一个非左上角视图内的可达点；检查日志显示实际选中视图、屏幕点击点、最终坐标。再选一个已确认不可达点，检查只点一次、日志写“坐标不可达”、`TaskFailed` 交给调度器处理。该步骤不作为 CI 自动测试。

本轮已完成左上视图真机验证：沼泽 `296,85` 点选后稳定到 `348,78`（标定样本目标 `347,79`，每轴误差 1）。四角样本尚未录齐，非左上视图与不可达真机验收待补数据后执行。

## 实施注意

- 不把当前未提交的 `tasks/DailySignIn/script_task.py` 改动加入任何提交。
- JSON 暂含沼泽和域外灵岛的左上样本；右上、左下、右下视图由用户以后录入。视图数据缺失时失败是预期行为。
- 建木城目前只有一次目标测试，不足以作为完整地图标定；获得足够可达样本后再加入 `maps.json`。
