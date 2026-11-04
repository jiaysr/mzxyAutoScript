# This Python file uses the following encoding: utf-8
"""
副本页左侧列表的次数识别（纯逻辑，不连设备）

列表每行形如：

    [2]  沙狱[多人]

方括号里是**剩余挑战次数**，后面是副本名。列表可上下滚动，
OCR 会把次数和名字识别成两个相邻的文本框，这里按 y 坐标聚合成一行。

`CONFIG_FIELDS` 只登记当前已接入配置的 6 个副本；列表里的其它副本
（白原/青丘/赢土试炼等）也会识别出来，只是暂时没有对应配置字段。
"""
import re

# 左侧列表区域（1280x720）
LIST_ROI = (112, 175, 400, 430)
# 同一行的两个文本框，中心 y 相差不会超过这个值
ROW_TOLERANCE = 20

# OCR 形近字修正（真机实测 / 已知易混字）
NAME_FIXES = (
    ('帝玉领', '帝玉岭'),
    ('帝玉領', '帝玉岭'),
    ('琉璃幻镜', '琉璃幻境'),
    ('琉璃幻培', '琉璃幻境'),
    ('嬴土', '赢土'),
    ('羸土', '赢土'),
    ('少狱', '沙狱'),
    ('沙獄', '沙狱'),
    ('単人', '单人'),
    ('單人', '单人'),
    ('雙人', '双人'),
    ('天淵靈島', '天渊灵岛'),
    ('至尊巔峰', '至尊巅峰'),
    ('島', '岛'),
)

# 已接入配置的副本：列表名 -> DungeonConfig 字段前缀
CONFIG_FIELDS = {
    '沙狱[多人]': 'shayu_multi',
    '沙狱[单人]': 'shayu_solo',
    '帝玉岭[多人]': 'diyuling_multi',
    '帝玉岭[单人]': 'diyuling_solo',
    '琉璃幻境': 'liuli_huanjing',
}
# 反向：字段前缀 -> 列表里的副本名
FIELD_NAMES = {field: name for name, field in CONFIG_FIELDS.items()}

_CJK = re.compile(r'[\u4e00-\u9fa5]')
_COUNT_ONLY = re.compile(r'[\[【]?\s*(\d+)\s*[\]】]?')
_COUNT_WITH_NAME = re.compile(r'[\[【]?\s*(\d+)\s*[\]】]?\s*(.+)')


def normalize_name(text: str) -> str:
    """去掉空白、统一括号、修正形近字"""
    text = re.sub(r'\s+', '', text or '')
    for wrong, right in NAME_FIXES:
        text = text.replace(wrong, right)
    return text.replace('【', '[').replace('】', ']')


def parse_dungeon_rows(items, with_pos: bool = False) -> list:
    """
    把 OCR 结果聚合成 [(副本名, 剩余次数), ...]

    :param items: [(text, y), ...]，y 为文本框中心相对列表 ROI 的坐标
    :param with_pos: True 时返回 [(副本名, 剩余次数, 行中心 y), ...]
    """
    rows = []
    for text, y in sorted(items, key=lambda item: item[1]):
        if rows and abs(y - rows[-1][0]) <= ROW_TOLERANCE:
            rows[-1][1].append(text)
        else:
            rows.append([y, [text]])

    result = []
    for row_y, texts in rows:
        name, count = None, None
        for raw in texts:
            text = (raw or '').strip()
            if not text:
                continue
            combined = _COUNT_WITH_NAME.fullmatch(text)
            if combined and _CJK.search(combined.group(2)):
                # OCR 把「[2] 沙狱[多人]」识别成一个框
                count = int(combined.group(1))
                name = normalize_name(combined.group(2))
                continue
            if _CJK.search(text):
                name = normalize_name(text)
                continue
            only = _COUNT_ONLY.fullmatch(text)
            if only:
                count = int(only.group(1))
        if name and count is not None:
            if with_pos:
                result.append((name, count, int(row_y)))
            else:
                result.append((name, count))
    return result


def dungeon_name_matches(ocr_name: str, target: str) -> bool:
    """副本名匹配：先做形近字修正，再精确比较（避免多人/单人互相误判）"""
    return normalize_name(ocr_name) == normalize_name(target)


def parse_privilege_count(text: str) -> int | None:
    """
    解析底部「特权1级额外挑战剩余次数：1」里的剩余次数。

    OCR 有时会把「次数」也认错，所以优先取冒号后的数字，兜底取整串最后一个数字。
    """
    if not text:
        return None
    match = re.search(r'次数\s*[：:]?\s*(\d+)', text)
    if match:
        return int(match.group(1))
    # 「次数」认出来了但后面没数字：说明这次没读到剩余次数，别拿「特权1级」的 1 当结果
    if '次数' in text:
        return None
    numbers = re.findall(r'\d+', text)
    return int(numbers[-1]) if numbers else None


def config_counts(counts: dict) -> dict:
    """把「副本名 -> 次数」转成「配置字段前缀 -> 次数」，只保留已接入配置的副本"""
    return {field: counts[name]
            for name, field in CONFIG_FIELDS.items()
            if name in counts}
