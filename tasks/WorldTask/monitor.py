# This Python file uses the following encoding: utf-8
"""
任务完成提示监测（接任务之后、做任务全程都要开着）

游戏在左下角聊天栏打系统消息：`[系]任务：【跨服】世界之巅已完成`。
和脚本 `tests/task_complete_monitor.py` 的做法一致，分两级：

1. 先用「[系]任务」前缀模板做一次便宜的模板匹配（不命中就直接返回，几乎不耗时）；
2. 命中后再对整行 OCR，用 `任务[:：]xxx已完成` 抽任务名。

为什么要在「接任务之前」就开：接取某些任务时会立刻判定完成（游戏会当场打已完成），
这些提示只在聊天栏出现一次、几秒后就被刷走，漏了就等于白跑一趟，所以
ScriptTask.screenshot() 每帧都过一遍，接任务期间也不会漏。
"""
import re
import time

TASK_RE = re.compile(r'任务\s*[:：]\s*(?P<name>.+?)\s*已完成')


def normalize_text(text: str) -> str:
    """去掉换行/全角空格/全角冒号差异，方便正则匹配"""
    text = (text or '').replace('：', ':').replace('\u3000', ' ')
    return re.sub(r'\s+', '', text)


def extract_task_name(text: str):
    """
    从系统消息里抽任务名

    `[系]任务：【跨服】世界之巅已完成` -> `【跨服】世界之巅`
    """
    match = TASK_RE.search(normalize_text(text))
    if not match:
        return None
    name = match.group('name').strip(':;；,，.。')
    return name or None


class TaskCompleteMonitor:
    """
    任务完成提示监测器

    :param trigger_rule: 「[系]任务」前缀模板（RuleImage，roi_back = 聊天栏那一行）
    :param text_rule: 聊天栏那一行的 OCR 规则（RuleOcr，roi = 同一行）
    :param dedup: 同一个任务名在这段时间内只报一次（秒）
    """

    def __init__(self, trigger_rule, text_rule, dedup: float = 5.0):
        self.trigger_rule = trigger_rule
        self.text_rule = text_rule
        self.dedup = dedup
        self.names: list = []          # 识别到的任务名，按出现顺序
        self.last_text = ''            # 最近一次 OCR 原文（排查用）
        self._last_name = None
        self._last_at = 0.0

    def detect(self, image) -> list:
        """
        检测一帧里有没有「任务完成」提示

        :param image: RGB 整屏截图
        :return: 新识别到的任务名列表（通常 0 或 1 个）
        """
        if image is None:
            return []
        if not self.trigger_rule.match(image):
            return []

        text = self.text_rule.ocr_single_line(image)
        self.last_text = text or ''
        name = extract_task_name(text)
        if not name:
            return []

        now = time.time()
        if name == self._last_name and now - self._last_at < self.dedup:
            return []
        self._last_name, self._last_at = name, now
        self.names.append(name)
        return [name]
