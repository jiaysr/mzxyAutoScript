# -*- coding: utf-8 -*-
"""
世界任务 NPC 识别单测（不连设备）

素材来自标注工具（tasks/WorldTask/res/）：
- C_NEW / C_ITEM_2：小地图上两个 NPC 的点位（按「视口在右上角」录制）
- I_NEW / I_ITEM_2：对话框特征（标题 + 第一行台词）

这里验证三件事：
1. 两个对话框模板**能互相区分**（拿录制图糊成整屏，I_NEW 只认 I_NEW 的帧）；
2. 点位点的是录制规则里的坐标，不是别处；
3. 找 NPC 的顺序：先判地图（不在就传送）-> 小地图拖到右上角 -> 点位 -> 模板确认；
   已经开着别的 NPC 对话框时，先关掉再点。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_npc.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_npc.py
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.WorldTask.assets import WorldTaskAssets
from tasks.WorldTask.script_task import ScriptTask

SCREEN = (720, 1280, 3)  # (h, w, c)，框架里是 RGB

# 标注工具录下来的区域（与 res/*.json 一一对应）
NEW_CLICK_ROI = (674, 153, 14, 19)
ITEM_CLICK_ROI = (703, 129, 26, 25)
NEW_DIALOG_ROI = (618, 178, 198, 89)
ITEM_DIALOG_ROI = (616, 113, 198, 89)


def load_bgr(path) -> np.ndarray:
    """中文路径用 imdecode，避免 cv2.imread 读不到"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def rgb(path) -> np.ndarray:
    return cv2.cvtColor(load_bgr(path), cv2.COLOR_BGR2RGB)


def frame_with(roi, image: np.ndarray) -> np.ndarray:
    """把模板按 roi 贴到纯色整屏上，模拟「对话框正开着」的截图"""
    x, y, w, h = roi
    screen = np.full(SCREEN, 40, dtype=np.uint8)
    screen[y:y + h, x:x + w] = image
    return screen


def make_task(image=None) -> ScriptTask:
    """只带 NPC 识别所需部件的任务实例"""
    task = ScriptTask.__new__(ScriptTask)
    device = SimpleNamespace(image=image)
    task.device = device
    task.maybe_screenshot = lambda soft_skip=False: None
    return task


class RecordingDevice:
    """记录点击，不真的点"""

    image = None

    def __init__(self):
        self.clicks = []

    def click(self, x=None, y=None, control_name=None, **kwargs):
        self.clicks.append((x, y, control_name))

    def click_record_clear(self):
        pass

    def sleep(self, seconds):
        pass


# ---------------------------------------------------------------- 素材一致性
def test_assets_match_recorded_json():
    """assets.py 必须和 res/*.json 对得上（改了 JSON 忘了重新生成就会在这里炸）"""
    res = PROJECT_ROOT / 'tasks' / 'WorldTask' / 'res'
    assets = WorldTaskAssets()

    clicks = {item['itemName']: item for item in
              json.loads((res / 'click.json').read_text(encoding='utf-8'))}
    images = {item['itemName']: item for item in
              json.loads((res / 'image.json').read_text(encoding='utf-8'))}

    assert clicks['new']['roiFront'] == '674,153,14,19'
    assert clicks['item_2']['roiFront'] == '703,129,26,25'
    assert tuple(assets.C_NEW.roi_front) == NEW_CLICK_ROI
    assert tuple(assets.C_ITEM_2.roi_front) == ITEM_CLICK_ROI

    # RuleImage 的 roi_front 是可变 list（匹配成功后会写回命中位置），比较前先 tuple 化
    assert tuple(assets.I_NEW.roi_front) == NEW_DIALOG_ROI
    assert tuple(assets.I_ITEM_2.roi_front) == ITEM_DIALOG_ROI
    for item in images.values():
        path = res / item['imageName']
        assert path.is_file(), f'缺模板图 {path}'
        assert path.stat().st_size > 0


# ---------------------------------------------------------------- 对话框识别
def test_dialog_templates_discriminate():
    """拿录制素材糊成整屏，I_NEW / I_ITEM_2 必须只认自己的那一帧"""
    task = make_task()

    new_frame = frame_with(NEW_DIALOG_ROI, rgb(task.I_NEW.file))
    item_frame = frame_with(ITEM_DIALOG_ROI, rgb(task.I_ITEM_2.file))
    blank = np.full(SCREEN, 40, dtype=np.uint8)

    for image, expect in ((new_frame, 'new'), (item_frame, 'item_2'), (blank, None)):
        task.device.image = image
        actual = task.npc_dialog()
        assert actual == expect, f'npc_dialog() = {actual}, expect {expect}'

    # 交叉验证：I_NEW 在任务榜那一帧上必须低于阈值
    assert not task.I_NEW.match(item_frame), 'I_NEW 在任务榜对话框上误命中'
    assert not task.I_ITEM_2.match(new_frame), 'I_ITEM_2 在守门人对话框上误命中'


def test_dialog_timeout_returns_none():
    """对话框没出现时 npc_dialog 返回 None（不能抛异常）"""
    task = make_task(np.full(SCREEN, 40, dtype=np.uint8))
    assert task.npc_dialog() is None


# ---------------------------------------------------------------- 点 NPC
def test_click_recorded_position_and_order():
    """open_npc_dialog 的顺序：读坐标 -> 小地图到右上角 -> 点录制坐标 -> 主动关小地图 -> 等反应"""
    task = make_task()
    task.device = RecordingDevice()
    task.screenshot = lambda: None
    task.appear = lambda target, interval=None, threshold=None: False
    task.npc_dialog = lambda: 'new'

    calls = []
    task.map_current_location = lambda image=None: calls.append('location') or ('域外灵岛', 100, 100)
    task.minimap_top_right = lambda: calls.append('minimap') or True
    task.close_minimap_if_open = lambda: calls.append('close') or True
    task.wait_npc_response = lambda npc, before, timeout=None: calls.append('wait') or True

    assert task.open_npc_dialog('new') is True
    assert calls[:3] == ['location', 'minimap', 'close'], calls
    assert calls[-1] == 'wait', '关完小地图才去等反应'
    assert len(task.device.clicks) == 1, '只该点一次'

    x, y, name = task.device.clicks[0]
    fx, fy, fw, fh = NEW_CLICK_ROI
    assert fx <= x <= fx + fw and fy <= y <= fy + fh, f'点歪了: ({x},{y}) 不在 C_NEW 里'
    assert name == 'new', f'control_name 应为录制规则的 name，实际 {name}'


def test_open_npc_dialog_retries_when_no_response():
    """点完没反应（没点中）就重试，最多 NPC_RETRY 次"""
    task = make_task()
    task.device = RecordingDevice()
    task.screenshot = lambda: None
    task.appear = lambda target, interval=None, threshold=None: False
    task.map_current_location = lambda image=None: ('域外灵岛', 100, 100)
    task.minimap_top_right = lambda: True
    task.close_minimap_if_open = lambda: True
    task.NPC_RETRY = 3
    # 前两次没反应，第三次成功
    responses = [False, False, True]
    task.wait_npc_response = lambda npc, before, timeout=None: responses.pop(0)

    assert task.open_npc_dialog('new') is True
    assert len(task.device.clicks) == 3, f'应该点 3 次，实际 {len(task.device.clicks)}'


def test_open_npc_dialog_gives_up_after_retry():
    task = make_task()
    task.device = RecordingDevice()
    task.screenshot = lambda: None
    task.appear = lambda target, interval=None, threshold=None: False
    task.map_current_location = lambda image=None: ('域外灵岛', 100, 100)
    task.minimap_top_right = lambda: True
    task.close_minimap_if_open = lambda: True
    task.NPC_RETRY = 2
    task.wait_npc_response = lambda npc, before, timeout=None: False

    assert task.open_npc_dialog('new') is False
    assert len(task.device.clicks) == 2


# ---------------------------------------------------------------- 点完之后的判定
def make_wait_task(dialogs, locations, move_timeout=0.05,
                   stuck_timeout=0.05, dialog_timeout=1.0):
    """造一个专门测 wait_npc_response 的实例：对话框/坐标按序列喂进去"""
    task = make_task()
    task.device = SimpleNamespace(image=None, sleep=lambda seconds: None)
    task.screenshot = lambda: None
    task.NPC_MOVE_TIMEOUT = move_timeout
    task.NPC_STUCK_TIMEOUT = stuck_timeout
    task.NPC_DIALOG_TIMEOUT = dialog_timeout
    task.npc_dialog = lambda: dialogs.pop(0) if dialogs else None
    task.map_current_location = lambda image=None: locations.pop(0) if locations else None
    return task


BEFORE = ('域外灵岛', 100, 100)


def test_wait_response_ok_when_dialog_pops_immediately():
    """有的点位点完人不动、对话框直接弹（真机第一次就是这样）"""
    task = make_wait_task(dialogs=['new'], locations=[])
    assert task.wait_npc_response('new', BEFORE) is True


def test_wait_response_fails_when_nothing_moves():
    """坐标一动不动、对话框也没有 = 没点中，必须失败交给上层重试"""
    task = make_wait_task(dialogs=[], locations=[('域外灵岛', 100, 100)] * 50)
    assert task.wait_npc_response('new', BEFORE) is False


def test_wait_response_waits_after_character_moves():
    """坐标动了 = 人物在走过去，之后弹出对话框算成功"""
    task = make_wait_task(dialogs=[None, None, 'new'],
                          locations=[('域外灵岛', 110, 100)] * 50)
    assert task.wait_npc_response('new', BEFORE) is True


def test_wait_response_fails_when_character_stuck():
    """走两步又停住、对话框始终不出 = 卡住，别等到超时"""
    locations = [('域外灵岛', 110, 100)] + [('域外灵岛', 110, 100)] * 200
    task = make_wait_task(dialogs=[], locations=locations,
                          stuck_timeout=0.05, dialog_timeout=5.0)
    assert task.wait_npc_response('new', BEFORE) is False


def test_wait_response_rejects_other_dialog():
    """点出的是另一个 NPC 的对话框：立刻失败，先关掉再说"""
    task = make_wait_task(dialogs=['item_2'], locations=[])
    assert task.wait_npc_response('new', BEFORE) is False


def test_location_moved_tolerance():
    task = make_task()
    task.NPC_MOVE_TOLERANCE = 3
    assert task._location_moved(('域外灵岛', 100, 100), ('域外灵岛', 100, 100)) is False
    assert task._location_moved(('域外灵岛', 100, 100), ('域外灵岛', 103, 98)) is False
    assert task._location_moved(('域外灵岛', 100, 100), ('域外灵岛', 104, 100)) is True
    # 坐标读不出来（None）不算移动，避免误判成「点中了」
    assert task._location_moved(('域外灵岛', 100, 100), None) is False
    assert task._location_moved(None, ('域外灵岛', 120, 100)) is False


# ---------------------------------------------------------------- 活动弹窗 tab
# 真机 OCR 实测的 tab 布局（2026-11-05 四张截图）
TABS_LEFT = [('世界之巅', 234, 128), ('周末限时', 473, 116),
             ('跨服任务', 712, 116), ('灭世铸烬', 951, 116)]
TABS_RIGHT = [('灭世铸烬', 293, 116), ('荣誉商店', 533, 117),
              ('荣耀币商店', 771, 116), ('玩法说明', 1011, 116)]
TABS_GARBAGE = [('主1无补天赋', 155, 138), ('<任务>矢量战神', 532, 133),
                ('65之巅守门人', 595, 104), ('赤霄', 1108, 102)]


def make_tab_task(pages):
    """
    造一个测 tab 切换的实例

    :param pages: 每次读 tab 条返回的列表（按读取顺序消耗，最后一个会一直重复）
    """
    task = make_task()
    task.device = RecordingDevice()
    task.screenshot = lambda: None
    state = {'index': 0, 'swipes': []}

    def ocr_items():
        index = min(state['index'], len(pages) - 1)
        return list(pages[index])

    def swipe_tabs(forward=True):
        state['swipes'].append(forward)
        before = task.tab_signature()
        state['index'] = min(state['index'] + 1, len(pages) - 1)
        return before != task.tab_signature()

    task._ocr_items = ocr_items
    task.swipe_tabs = swipe_tabs
    task.state = state
    return task


def test_switch_tab_clicks_when_visible():
    """tab 已经在视野里：直接点，不滑"""
    task = make_tab_task([TABS_LEFT])
    clicks = []
    task.click_tab = lambda name, coord=None: clicks.append((name, coord)) or True

    assert task.switch_tab('跨服任务') is True
    assert clicks == [('跨服任务', (712, 116))]
    assert task.state['swipes'] == []


def test_switch_tab_swipes_until_found():
    """目标在右边看不见：往右翻一页后点中"""
    task = make_tab_task([TABS_LEFT, TABS_RIGHT])
    clicks = []
    task.click_tab = lambda name, coord=None: clicks.append((name, coord)) or True

    assert task.switch_tab('荣誉商店') is True
    assert task.state['swipes'] == [True]
    assert clicks == [('荣誉商店', (533, 117))]


def test_switch_tab_scrolls_back_when_at_right_end():
    """世界之巅是第一个 tab：tab 条停在右边时只能往左翻回来"""
    task = make_tab_task([TABS_RIGHT, TABS_LEFT])
    clicks = []
    task.click_tab = lambda name, coord=None: clicks.append((name, coord)) or True

    assert task.switch_tab('世界之巅') is True
    # 目标在最左边 -> 第一下就该往左翻，不能傻乎乎继续往右翻
    assert task.state['swipes'] == [False], task.state['swipes']
    assert clicks == [('世界之巅', (234, 128))]


def test_tab_direction():
    """方向判断：目标在可见范围左边往左翻、右边往右翻"""
    task = make_tab_task([TABS_LEFT])
    assert task.tab_direction('世界之巅') is True        # 已在视野，方向无意义
    assert task.tab_direction('荣誉商店') is True        # 在右边
    assert task.tab_direction('不存在') is None

    task = make_tab_task([TABS_RIGHT])
    assert task.tab_direction('世界之巅') is False       # 在左边
    assert task.tab_direction('跨服任务') is False       # 也在左边
    assert task.tab_direction('玩法说明') is True        # 已在视野


def test_switch_tab_gives_up_when_never_found():
    """翻遍了也没有这个 tab：返回 False，不无限循环"""
    task = make_tab_task([TABS_RIGHT])
    task.click_tab = lambda name, coord=None: True

    assert task.switch_tab('不存在的tab') is False
    assert len(task.state['swipes']) <= 2, f'空翻了太多次: {task.state["swipes"]}'


def test_ensure_activity_tab_verifies_content():
    """切到世界之巅之后要等黄帝立绘出现，不能点完就算数"""
    task = make_task()
    task.screenshot = lambda: None
    task.device = SimpleNamespace(image=None, sleep=lambda seconds: None)
    task.switch_tab = lambda name, max_swipe=3: True

    task.appear = lambda target, interval=None, threshold=None: True
    assert task.ensure_activity_tab() is True

    task.appear = lambda target, interval=None, threshold=None: False
    task.find_tab = lambda name: (234, 128)     # tab 还在视野里，但内容没出来
    assert task.ensure_activity_tab() is False


def test_activity_panel_open_detection():
    """有黄帝立绘=开着；立绘没了但 tab 条认得 2 个以上已知 tab 也算开着"""
    task = make_task()
    task.screenshot = lambda: None
    task.appear = lambda target, interval=None, threshold=None: False
    task._ocr_items = lambda: list(TABS_LEFT)
    assert task.activity_panel_open() is True

    task._ocr_items = lambda: list(TABS_GARBAGE)
    assert task.activity_panel_open() is False

    # 只剩 1 个 tab 时不认（可能只是路过页面里的文字）
    task._ocr_items = lambda: [('世界之巅', 234, 128), ('主1无补天赋', 155, 138)]
    assert task.activity_panel_open() is False

    # 黄帝立绘在就直接算开着（切过 tab 之后 tab 条也可能读不全）
    task.appear = lambda target, interval=None, threshold=None: True
    task._ocr_items = lambda: []
    assert task.activity_panel_open() is True


# ---------------------------------------------------------------- 地图与找 NPC
def test_find_npc_teleports_only_when_outside_location():
    def build(location, teleport_result=True):
        task = make_task()
        task.config = SimpleNamespace(
            world_task=SimpleNamespace(world_task_config=SimpleNamespace(location='域外灵岛')))
        task.device = SimpleNamespace(image=None, sleep=lambda seconds: None)
        task.map_current_location = lambda: location
        teleports = []
        task.map_teleport = lambda name: teleports.append(name) or teleport_result
        return task, teleports

    # 已经在目标地图：不传送
    task, teleports = build(('域外灵岛', 285, 35))
    assert task.ensure_location() is True
    assert teleports == []

    # 不在目标地图：传送到配置里的地点
    task, teleports = build(('沼泽', 427, 59))
    assert task.ensure_location() is True
    assert teleports == ['域外灵岛']

    # 传送失败
    task, teleports = build(('沼泽', 427, 59), teleport_result=False)
    assert task.ensure_location() is False

    # 地点读不出来（OCR 只读到坐标）：不传送，先继续（按乱码传送会白开世界地图）
    task, teleports = build(None)
    assert task.ensure_location() is True
    assert teleports == [], '读不出地点时不该盲传'


def test_find_npc_closes_other_dialog_first():
    """已经开着别的 NPC 对话框时，先关掉再点，避免点不出来"""
    task = make_task()
    events = []
    dialogs = ['item_2', None]  # 第一次问：任务榜开着；关完再问：没了

    task.ensure_location = lambda name=None: True
    task.npc_dialog = lambda: dialogs.pop(0) if dialogs else None
    task.close_npc_dialog = lambda: events.append('close') or True
    task.open_npc_dialog = lambda npc, timeout=None: events.append(f'open:{npc}') or True

    assert task.find_npc('new') is True
    assert events == ['close', 'open:new'], events


def main():
    cases = [
        test_assets_match_recorded_json,
        test_dialog_templates_discriminate,
        test_dialog_timeout_returns_none,
        test_click_recorded_position_and_order,
        test_open_npc_dialog_retries_when_no_response,
        test_open_npc_dialog_gives_up_after_retry,
        test_wait_response_ok_when_dialog_pops_immediately,
        test_wait_response_fails_when_nothing_moves,
        test_wait_response_waits_after_character_moves,
        test_wait_response_fails_when_character_stuck,
        test_wait_response_rejects_other_dialog,
        test_location_moved_tolerance,
        test_switch_tab_clicks_when_visible,
        test_switch_tab_swipes_until_found,
        test_switch_tab_scrolls_back_when_at_right_end,
        test_tab_direction,
        test_switch_tab_gives_up_when_never_found,
        test_ensure_activity_tab_verifies_content,
        test_activity_panel_open_detection,
        test_find_npc_teleports_only_when_outside_location,
        test_find_npc_closes_other_dialog_first,
    ]
    failed = 0
    for case in cases:
        try:
            case()
        except AssertionError as e:
            failed += 1
            print(f'FAIL {case.__name__}: {e}')
        else:
            print(f'PASS {case.__name__}')
    if failed:
        print(f'\n{failed} / {len(cases)} failed')
        sys.exit(1)
    print(f'\nall {len(cases)} passed')


if __name__ == '__main__':
    main()
