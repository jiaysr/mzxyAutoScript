# This Python file uses the following encoding: utf-8
"""
从一张 1280x720 真机截图里「录制」一个技能图标

用途
----
新技能/换图标时，不用手工截图裁剪：告诉它截图、技能位（1~7）、技能 id，
它按「判定区域 + 内部图标区」裁出 40x40 的图标（带圆形透明边），存进
`tasks/Battle/res/skills/`，并打印可以直接粘进 `skills.json` 的条目。

为什么裁内部而不是整个按钮
--------------------------
按钮外圈是金属边框 + 高光，所有技能都一样，带着它算相似度会把不相干的技能拉得很像。
只取中间 40x40 的图标本体（再用圆形 alpha 去掉四个角），才能按「画面内容」区分。

用法（项目根目录）：
    toolkit\\python.exe tasks\\Battle\\tools\\record_skill_icon.py ^
        --image "C:\\...\\MuMu-20261025-203039-754.png" --slot 1 --id badao_1 ^
        --name 力斩 --class badao --priority 1

    --slot      技能位序号（1~7，和游戏里从左下往右上数一致）
    --id        技能 id（建议 <职业>_<序号>，比如 badao_1；不要和已有 id 冲突）
    --name      技能名（只用于打印建议的 json 条目）
    --class     职业 id
    --priority  默认优先级（数字越小越先放）
    --overwrite 覆盖已存在的图标文件（默认拒绝，避免把真图标覆盖成占位图）
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.Battle.slots import (BATTLE_SKILL_CHECK_REGIONS, SKILL_ICON_INSET,
                                SKILL_ICON_SIZE, slot_name)

ICON_DIR = PROJECT_ROOT / 'tasks' / 'Battle' / 'res' / 'skills'
CATALOG = PROJECT_ROOT / 'tasks' / 'Battle' / 'skills.json'


def crop_icon(image: Image.Image, slot: int) -> Image.Image:
    """按技能位裁出图标本体，并套圆形 alpha（去掉按钮外圈/四角）"""
    x, y, w, h = BATTLE_SKILL_CHECK_REGIONS[slot]
    ix, iy, iw, ih = SKILL_ICON_INSET
    icon = image.crop((x + ix, y + iy, x + ix + iw, y + iy + ih)).convert('RGBA')

    mask = Image.new('L', icon.size, 0)
    draw = ImageDraw.Draw(mask)
    margin = 1
    draw.ellipse((margin, margin, iw - 1 - margin, ih - 1 - margin), fill=255)
    icon.putalpha(mask)
    return icon
def main() -> int:
    parser = argparse.ArgumentParser(description='从真机截图录制一个技能图标')
    parser.add_argument('--image', type=Path, required=True, help='1280x720 截图')
    parser.add_argument('--slot', type=int, required=True, help='技能位 1~7')
    parser.add_argument('--id', type=str, required=True, help='技能 id，比如 badao_1')
    parser.add_argument('--name', type=str, default='', help='技能名（只用于打印 json）')
    parser.add_argument('--class', dest='class_id', type=str, default='',
                         help='职业 id')
    parser.add_argument('--priority', type=int, default=0, help='默认优先级')
    parser.add_argument('--overwrite', action='store_true', help='覆盖已存在的图标')
    args = parser.parse_args()

    slot = args.slot - 1
    if slot not in BATTLE_SKILL_CHECK_REGIONS:
        print(f'技能位 {args.slot} 不存在（只有 1~{len(BATTLE_SKILL_CHECK_REGIONS)}）')
        return 1

    image = Image.open(args.image).convert('RGB')
    if image.size != (1280, 720):
        print(f'警告：截图尺寸 {image.size} 不是 1280x720，坐标可能对不上')

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    filename = f'skill_{args.id}.png'
    path = ICON_DIR / filename
    if path.exists() and not args.overwrite:
        print(f'{path.relative_to(PROJECT_ROOT)} 已存在，加 --overwrite 才覆盖')
        return 1

    icon = crop_icon(image, slot)
    icon.save(path)
    print(f'已保存 {path.relative_to(PROJECT_ROOT)}  ({SKILL_ICON_SIZE[0]}x{SKILL_ICON_SIZE[1]})')

    entry = {
        'id': args.id,
        'class': args.class_id or '<职业 id>',
        'name': args.name or '<技能名>',
        'description': f'{args.class_id or "<职业>"} 第 {args.slot} 个技能',
        'icon': filename,
        'cooldown': 0,
        'default_priority': args.priority or '<数字>',
    }
    print('\n可以粘进 tasks/Battle/skills.json 的条目：')
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print(f'\n技能位 {args.slot}（{slot_name(slot)}）'
          f'判定区域 {BATTLE_SKILL_CHECK_REGIONS[slot]}，'
          f'图标区 {SKILL_ICON_INSET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
