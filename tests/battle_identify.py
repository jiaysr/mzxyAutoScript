# -*- coding: utf-8 -*-
"""
技能位识别工具：连设备截一张图，认出 7 个技能位分别带了哪个技能，并算出释放顺序

用途
----
1. 换成真实技能图标后，用它看分数分布，决定 `identify_threshold`（配置里的「识别相似度下限」）；
2. 排查「为什么开战时先放这个技能」——打印的释放计划就是任务里实际会用的顺序；
3. 录素材：`--save` 存一张带框的标注图，方便对照坐标。

用法（项目根目录，游戏切到能看到技能栏的画面）：
    toolkit\\python.exe tests\\battle_identify.py
    toolkit\\python.exe tests\\battle_identify.py --config oas1 --save log/battle_slots.png
    toolkit\\python.exe tests\\battle_identify.py --preset preset_1    # 指定看哪个预设的计划
    toolkit\\python.exe tests\\battle_identify.py --threshold 0.8      # 临时试用别的阈值
    toolkit\\python.exe tests\\battle_identify.py --list               # 只打印技能位坐标，不连设备
    toolkit\\python.exe tests\\battle_identify.py --image path.png     # 用一张截图识别（不连设备）

分数怎么看
----------
- 相似度 >= 阈值 且 与第二名差距 >= margin -> 认出来了；
- 打印里 `(0.93/次0.41)` 表示 分数 0.93、第二名 0.41；差距太小会被判成未识别；
- 认错优先于认不出：宁可看到「未识别」，也不要看到认错的技能 —— 认错会按错的优先级放技能。
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle import slots as battle_slots
from tasks.Battle.catalog import load_catalog
from tasks.Battle.planner import build_plan, plan_summary
from tasks.Battle.config import DEFAULT_PRESET_ID
from tasks.Battle.presets import resolve_preset
from tasks.Battle.recognizer import (DEFAULT_MARGIN, DEFAULT_THRESHOLD, SkillRecognizer,
                                     format_matches)
from tasks.Battle.slots import SKILL_ICON_INSET

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)
COLOR_OK = (70, 210, 70)
COLOR_UNKNOWN = (240, 170, 60)


def cjk_font(size: int):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def read_battle_config(config_name: str):
    """读配置里的战斗组（读不到就用默认值，方便没装环境时也能跑）"""
    try:
        from module.config.config import Config
        config = Config(config_name)
        battle = getattr(config, 'battle', None)
        if battle is not None:
            return battle
    except Exception as e:  # noqa: BLE001
        print(f'读取配置 {config_name} 失败（用默认值继续）：{e}')
    from tasks.Battle.config import Battle
    return Battle()


def annotate(image_rgb: np.ndarray, matches, plan) -> Image.Image:
    """在截图上画技能位的框和识别结果"""
    image = Image.fromarray(image_rgb.astype(np.uint8)).convert('RGB')
    draw = ImageDraw.Draw(image)
    font = cjk_font(14)
    plan_slots = {entry.slot: index + 1 for index, entry in enumerate(plan)}
    for slot, region in sorted(battle_slots.BATTLE_SKILL_CHECK_REGIONS.items()):
        x, y, w, h = region
        match = matches.get(slot)
        ok = match is not None and match.skill_id
        color = COLOR_OK if ok else COLOR_UNKNOWN
        draw.rectangle((x, y, x + w, y + h), outline=color, width=2)
        # 识别实际比较的是中间这块「图标本体」，画出来方便对照
        ix, iy, iw, ih = SKILL_ICON_INSET
        draw.rectangle((x + ix, y + iy, x + ix + iw, y + iy + ih),
                       outline=(120, 180, 255), width=1)
        label = f'{slot + 1}'
        if ok:
            label += f' {match.skill_id.split("_")[-2].upper()}{match.skill_id.split("_")[-1]}'
        order = plan_slots.get(slot)
        if order:
            label += f' #{order}'
        draw.text((x, max(0, y - 16)), label, font=font, fill=color)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description='技能位识别（7 个位置分别带了哪个技能）')
    parser.add_argument('--config', default='oas1', help='config/<name>.json 的配置名')
    parser.add_argument('--preset', default='', help='用哪个战斗预设 id（默认用当前生效的）')
    parser.add_argument('--threshold', type=float, default=None,
                        help=f'相似度下限，默认用配置里的值（兜底 {DEFAULT_THRESHOLD}）')
    parser.add_argument('--margin', type=float, default=DEFAULT_MARGIN,
                        help=f'与第二名的差距下限（默认 {DEFAULT_MARGIN}）')
    parser.add_argument('--save', type=Path, default=None, help='保存标注图到该路径')
    parser.add_argument('--image', type=Path, default=None,
                        help='不连设备，直接读一张 1280x720 截图来识别')
    parser.add_argument('--list', action='store_true', help='只打印技能位坐标，不识别')
    args = parser.parse_args()

    catalog = load_catalog()
    print(f'技能目录：{len(catalog.classes)} 个职业 / {len(catalog.skills)} 个技能')
    for slot, region in sorted(battle_slots.BATTLE_SKILL_CHECK_REGIONS.items()):
        print(f'  技能{slot + 1}: 判定区域 {region}  点击区域 {battle_slots.BATTLE_SKILL_REGIONS[slot]}')
    if args.list:
        return 0

    battle = read_battle_config(args.config)
    common = battle.common
    threshold = args.threshold if args.threshold is not None else float(common.identify_threshold)
    preset = resolve_preset(battle, args.preset or DEFAULT_PRESET_ID, catalog)
    order = catalog.normalize_order(list(preset['order']), list(preset['disabled']))
    disabled = list(preset['disabled'])
    print(f'预设：{preset["name"]}（{preset["id"]}），'
          f'启用 {len(order)} 个技能，阈值 {threshold:.2f}，margin {args.margin:.2f}')

    if args.image is not None:
        import cv2
        image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
        if image is None:
            print(f'图片读取失败：{args.image}')
            return 1
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)  # 本项目约定 RGB
    else:
        from module.device.screenshot import Screenshot
        device = Screenshot(config=args.config)
        print(f'设备：{device.serial}')
        image = device.screenshot()

    recognizer = SkillRecognizer.from_catalog(catalog, threshold=threshold, margin=args.margin)
    matches = recognizer.identify(image, battle_slots.BATTLE_SKILL_CHECK_REGIONS)

    print('\n识别结果：')
    for slot in sorted(matches):
        match = matches[slot]
        skill = catalog.skill(match.skill_id) if match.skill_id else None
        name = f'{skill.name}（{skill.id}）' if skill else '未识别'
        print(f'  技能{slot + 1}: {name}  分数 {match.score:.3f}  第二名 {match.second:.3f}')

    plan = build_plan(matches, order, catalog, disabled=disabled)
    print(f'\n释放计划（{len(plan)} 个）：{plan_summary(plan)}')
    for index, entry in enumerate(plan, start=1):
        print(f'  #{index} {battle_slots.slot_name(entry.slot)}  {entry.name or "未识别"}')
    print(f'\n识别日志格式：{format_matches(matches, catalog)}')

    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        annotate(image, matches, plan).save(args.save)
        print(f'标注图已保存：{args.save}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
