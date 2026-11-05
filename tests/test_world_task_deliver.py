# -*- coding: utf-8 -*-
"""
世界任务「交付」单测（不连设备）

真机链路（2026-11-05 录制）：
1. 全部任务做完后**重启游戏**（框架 Restart 任务的 app_restart），上线后人物自动回到域外灵岛；
2. 找交付 NPC「世界之巅任务榜」→ 对话框里是**可交付任务列表**（完成的任务排最上面）；
3. 只看**第一行**：点它 → 详情页用录制好的 OCR 区域（`O_ITEM_5`）二次确认任务名；
4. 详情页右下角只有「确认/完成」才是交任务按钮（返回/领取不点）；
5. 点完回到列表页，再判第一行，一直交到第一行不是世界任务为止。

真机截图核对（离线 OCR 验证过）：列表首行 OCR 会被拆成 '【跨服】' + '世界印记'，
所以同一行要拼起来；`O_ITEM_5` 在详情页读到 '【跨服】世界印记'、按钮读到 '完成'；
`O_DELIVER_DETAIL`（「任务奖励」）只在详情页出现，用来判断详情页开没开。

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_world_task_deliver.py
装了 pytest 时也可以：python -m pytest tests/test_world_task_deliver.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.WorldTask.script_task import ScriptTask


class FakeDevice:
    image = None

    def __init__(self):
        self.clicks = []

    def click(self, x=None, y=None, control_name=None, **kwargs):
        self.clicks.append((x, y, control_name))

    def click_record_clear(self):
        pass

    def sleep(self, seconds):
        pass

    def stuck_record_clear(self):
        pass


class FakeOcrRule:
    """替身 OCR 规则：detect_and_ocr 吐预设文本，ocr_single_line 返回预设单行"""

    def __init__(self, texts=None, single='', roi=(0, 0, 0, 0), box_w=40, box_h=20):
        self.texts = texts or []
        self.single = single
        self.roi = list(roi)
        self.box_w = box_w
        self.box_h = box_h

    def detect_and_ocr(self, image, logDisplay=False):
        items = []
        for index, text in enumerate(self.texts):
            x0, y0 = 100 + index * 120, 100
            box = [[x0, y0], [x0 + self.box_w, y0],
                   [x0 + self.box_w, y0 + self.box_h], [x0, y0 + self.box_h]]
            items.append(SimpleNamespace(ocr_text=text, box=box))
        return items

    def ocr_single_line(self, image):
        return self.single


def make_task():
    task = ScriptTask.__new__(ScriptTask)
    task.device = FakeDevice()
    task.screenshot = lambda: None
    task.config = SimpleNamespace(
        world_task=SimpleNamespace(world_task_config=SimpleNamespace(
            location='域外灵岛', bonus_multiplier=None)))
    return task


# ---------------------------------------------------------------- 列表第一行
def test_first_row_joins_split_names():
    """OCR 把「【跨服】世界印记」拆成两段：同一行要拼起来，点整行中间"""
    task = make_task()
    rule = FakeOcrRule(texts=['【跨服】', '世界印记'])
    task.O_DELIVER_LIST = rule

    name, x, y = task.task_board_first_row()
    assert name == '【跨服】世界印记', name
    # 两个文本框中心分别是 120 和 240（x0=100/220、宽 40）-> 取整行平均 180
    assert x == 180, x
    assert y == 110, y


def test_first_row_empty():
    task = make_task()
    task.O_DELIVER_LIST = FakeOcrRule(texts=[])
    assert task.task_board_first_row() is None


def test_is_our_task():
    task = make_task()
    assert task.is_our_task('【跨服】世界印记') is True
    assert task.is_our_task('【跨服】为了荣耀') is True
    assert task.is_our_task('【跨服】恶魔印记') is False
    assert task.is_our_task('【技能】净化圣水') is False


# ---------------------------------------------------------------- 单个交付
def make_deliver_task(detail_sequence, titles, buttons):
    """
    造一个测交付的实例

    :param detail_sequence: deliver_detail_open() 的返回序列（True=详情页开着）
    :param titles: O_ITEM_5.ocr_single_line 的返回序列
    :param buttons: O_DELIVER_CONFIRM.detect_and_ocr 的文本序列（每次一个列表）
    """
    task = make_task()
    detail = list(detail_sequence)
    task.deliver_detail_open = lambda: detail.pop(0) if detail else False

    title_values = list(titles)

    class TitleRule:
        roi = [0, 0, 0, 0]

        def ocr_single_line(self, image):
            return title_values.pop(0) if title_values else ''

    task.O_ITEM_5 = TitleRule()

    button_values = list(buttons)

    class ButtonRule:
        roi = [0, 0, 0, 0]

        def detect_and_ocr(self, image, logDisplay=False):
            texts = button_values.pop(0) if button_values else []
            items = []
            for index, text in enumerate(texts):
                x0, y0 = 1000 + index * 60, 540
                box = [[x0, y0], [x0 + 40, y0], [x0 + 40, y0 + 20], [x0, y0 + 20]]
                items.append(SimpleNamespace(ocr_text=text, box=box))
            return items

    task.O_DELIVER_CONFIRM = ButtonRule()
    return task


def test_deliver_one_task_full_flow():
    """点行 -> 详情页确认任务名 -> 点完成 -> 回列表"""
    task = make_deliver_task(
        detail_sequence=[False, True, True, False],       # 详情页随后打开，交完关掉
        titles=['【跨服】世界印记', '【跨服】世界印记'],
        buttons=[['完成']],
    )
    assert task.deliver_one_task('世界印记', (600, 350)) is True
    names = [click[2] for click in task.device.clicks]
    assert names == ['deliver_task', 'deliver_confirm'], names


def test_deliver_one_task_accepts_confirm_text():
    """按钮文案是「确认」也算交任务"""
    task = make_deliver_task(detail_sequence=[True, False], titles=['【跨服】世界印记'],
                             buttons=[['确认']])
    assert task.deliver_one_task('世界印记', (600, 350)) is True
    assert [c[2] for c in task.device.clicks] == ['deliver_task', 'deliver_confirm']


def test_deliver_one_task_refuses_other_button():
    """右下角不是确认/完成（例如「领取」）：不点提交，返回 False"""
    task = make_deliver_task(detail_sequence=[True], titles=['【跨服】世界印记'],
                             buttons=[['领取']])
    assert task.deliver_one_task('世界印记', (600, 350)) is False
    assert [c[2] for c in task.device.clicks] == ['deliver_task'], '不该点提交'


def test_deliver_one_task_title_mismatch():
    """详情页标题对不上：不点提交（防止交错任务）"""
    task = make_deliver_task(detail_sequence=[True], titles=['【跨服】世界之心'],
                             buttons=[])
    assert task.deliver_one_task('世界印记', (600, 350)) is False
    assert [c[2] for c in task.device.clicks] == ['deliver_task']


# ---------------------------------------------------------------- 批量交付
def test_deliver_tasks_loop():
    """一直交到第一行不是世界任务为止"""
    task = make_task()
    task.find_npc = lambda npc, retry=2: True
    task.close_npc_dialog = lambda timeout=6: True
    rows = [('【跨服】世界印记', 600, 350), ('【跨服】兽毛之争', 600, 434),
            ('【跨服】恶魔印记', 600, 518)]
    task.task_board_first_row = lambda: rows.pop(0) if rows else None
    delivered = []
    task.deliver_one_task = lambda name, coord, timeout=10: delivered.append(name) or True

    assert task.deliver_tasks() is True
    assert delivered == ['【跨服】世界印记', '【跨服】兽毛之争'], delivered


def test_deliver_tasks_empty_list():
    """列表空 = 没有可交付的，算完成（重跑不会反复失败）"""
    task = make_task()
    task.find_npc = lambda npc, retry=2: True
    task.close_npc_dialog = lambda timeout=6: True
    task.task_board_first_row = lambda: None
    called = []
    task.deliver_one_task = lambda *args, **kwargs: called.append(1) or True

    assert task.deliver_tasks() is True
    assert called == []


def test_deliver_tasks_stops_when_delivery_fails():
    task = make_task()
    task.find_npc = lambda npc, retry=2: True
    task.close_npc_dialog = lambda timeout=6: True
    task.task_board_first_row = lambda: ('【跨服】世界印记', 600, 350)
    task.deliver_one_task = lambda name, coord, timeout=10: False

    assert task.deliver_tasks() is True, '交付失败也算走完，别让任务天天判失败'
    assert task.device.clicks == []


def test_deliver_tasks_needs_npc():
    task = make_task()
    task.find_npc = lambda npc, retry=2: False
    called = []
    task.task_board_first_row = lambda: called.append(1) or None

    assert task.deliver_tasks() is False
    assert called == [], 'NPC 都找不到就别往下走'


# ---------------------------------------------------------------- 收尾顺序
def test_do_world_peak_and_deliver_order():
    """做任务 -> 重启 -> 交付，顺序不能反"""
    task = make_task()
    calls = []
    task.run_world_peak_tasks = lambda: calls.append('tasks') or True
    task.restart_and_wait = lambda timeout=120: calls.append('restart') or True
    task.deliver_tasks = lambda: calls.append('deliver') or True

    assert task.do_world_peak_and_deliver() is True
    assert calls == ['tasks', 'restart', 'deliver'], calls


def test_do_world_peak_and_deliver_skips_when_tasks_fail():
    task = make_task()
    calls = []
    task.run_world_peak_tasks = lambda: False
    task.restart_and_wait = lambda timeout=120: calls.append('restart') or True
    task.deliver_tasks = lambda: calls.append('deliver') or True

    assert task.do_world_peak_and_deliver() is False
    assert calls == [], '任务没做完不该重启/交付'


# ---------------------------------------------------------------- 阵亡重跑 / 已全部完成
def test_flow_all_done_goes_straight_to_delivery():
    """任务页显示 7 个全完成（阵亡重跑 / 交付前重跑）：不进图，直接重启交付"""
    task = make_task()
    task.read_task_page_statuses = lambda names: [(n, 'done') for n in names]
    task.map_in_location = lambda name: False
    calls = []
    task.find_npc = lambda npc, retry=2: calls.append('npc') or True
    task.restart_and_wait = lambda timeout=120: calls.append('restart') or True
    task.deliver_tasks = lambda: calls.append('deliver') or True

    assert task.world_task_flow() is True
    assert calls == ['restart', 'deliver'], calls


def test_finish_bonus_keeps_card_on_death_rerun():
    """阵亡重跑：加量卡先留着（重跑还要用），别关"""
    task = make_task()
    task._rerun_requested = True
    called = []
    task.disable_bonus = lambda timeout=40: called.append(1) or True

    task.finish_bonus(bonus_enabled=True)
    assert called == []


def test_finish_bonus_disables_normally():
    task = make_task()
    task._rerun_requested = False
    called = []
    task.disable_bonus = lambda timeout=40: called.append(1) or True

    task.finish_bonus(bonus_enabled=True)
    assert called == [1]

    # 没启用过就别动加量页
    called.clear()
    task.finish_bonus(bonus_enabled=False)
    assert called == []


def main():
    cases = [
        test_first_row_joins_split_names,
        test_first_row_empty,
        test_is_our_task,
        test_deliver_one_task_full_flow,
        test_deliver_one_task_accepts_confirm_text,
        test_deliver_one_task_refuses_other_button,
        test_deliver_one_task_title_mismatch,
        test_deliver_tasks_loop,
        test_deliver_tasks_empty_list,
        test_deliver_tasks_stops_when_delivery_fails,
        test_deliver_tasks_needs_npc,
        test_do_world_peak_and_deliver_order,
        test_do_world_peak_and_deliver_skips_when_tasks_fail,
        test_flow_all_done_goes_straight_to_delivery,
        test_finish_bonus_keeps_card_on_death_rerun,
        test_finish_bonus_disables_normally,
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
