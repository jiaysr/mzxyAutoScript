# -*- coding: utf-8 -*-
"""
真机试打：按战斗预设的顺序攻击（测试用脚本）

它做的事就是调用战斗模块里对外暴露的那一个方法：

    SimpleBattle.battle_attack(times=..., preset=...)

流程：连设备 -> 截一帧识别技能栏 -> 按预设顺序打 N 轮（每个技能点一次 + 补普攻）
-> 打印实际点了哪些。脚本额外存一张标注图，标出「第几个点哪个技能位」。

用法（项目根目录，游戏停在能看到技能栏的画面）：
    toolkit\\python.exe tests\\battle_attack_once.py
    toolkit\\python.exe tests\\battle_attack_once.py --preset preset_1 --times 2
    toolkit\\python.exe tests\\battle_attack_once.py --no-cooldown    # 不看冷却，逐个点
    toolkit\\python.exe tests\\battle_attack_once.py --save log/attack.png

参数：
    --config     config/<name>.json 的配置名（默认 oas1）
    --preset     用哪个预设 id（默认按任务配置；脚本里没有任务，就是系统预设）
    --times      打几轮（默认 1）
    --interval   技能之间间隔秒（默认取配置 battle.common.cast_interval）
    --no-cooldown 关掉「只点冷却好的」（默认取配置）
    --no-attack  结尾不补普通攻击
    --save       保存一张带序号标注的截图
    --list       只打印预设和技能栏识别结果，不出手
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(PROJECT_ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.chdir(PROJECT_ROOT)

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)


def cjk_font(size: int):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def annotate(image_rgb, plan) -> "Image.Image":
    """在截图上标出「第几个点哪个技能位」"""
    from PIL import Image, ImageDraw
    from tasks.Battle.slots import BATTLE_SKILL_CHECK_REGIONS

    image = Image.fromarray(image_rgb).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = cjk_font(15)
    for order, entry in enumerate(plan, start=1):
        x, y, w, h = BATTLE_SKILL_CHECK_REGIONS[entry.slot]
        color = (70, 210, 70) if entry.recognized else (230, 170, 60)
        draw.rectangle((x, y, x + w, y + h), outline=color, width=2)
        text = f"{order}.{entry.name or '未识别'}"
        left, top, right, bottom = draw.textbbox((x - 2, max(0, y - 18)), text, font=font)
        draw.rectangle((left - 2, top - 1, right + 2, bottom + 1), fill=(0, 0, 0))
        draw.text((x - 2, max(0, y - 18)), text, font=font, fill=color)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description="按战斗预设顺序攻击（真机测试）")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 的配置名")
    parser.add_argument("--preset", default="", help="预设 id（默认按任务配置/系统预设）")
    parser.add_argument("--times", type=int, default=1, help="打几轮（默认 1）")
    parser.add_argument("--interval", type=float, default=None, help="技能之间间隔秒")
    parser.add_argument("--no-cooldown", action="store_true", help="不检查冷却，逐个点")
    parser.add_argument("--no-attack", action="store_true", help="结尾不补普通攻击")
    parser.add_argument("--save", type=Path, default=None, help="保存带标注的截图")
    parser.add_argument("--list", action="store_true", help="只打印预设和识别结果，不出手")
    args = parser.parse_args()

    from module.config.config import Config
    from tasks.Battle.catalog import load_catalog
    from tasks.Battle.config import DEFAULT_PRESET_ID
    from tasks.Battle.presets import list_presets, resolve_preset

    config = Config(args.config)
    battle = config.battle
    catalog = load_catalog()

    print("可用预设：")
    for preset in list_presets(battle, catalog):
        mark = "（系统）" if preset["builtin"] else ""
        print(f"  {preset['id']:10s} {preset['name']}{mark}"
              f"  技能 {len(preset['order'])} 个 / 禁用 {len(preset['disabled'])} 个")

    preset_id = args.preset or DEFAULT_PRESET_ID
    preset = resolve_preset(battle, preset_id, catalog)
    print(f"本次使用预设：{preset['name']}（{preset['id']}）")

    from module.device.screenshot import Screenshot
    from tasks.GameUi.navigation.battle import SimpleBattle

    device = Screenshot(config=args.config)
    print(f"设备: {device.serial}, 包名: {device.package}")

    task = SimpleBattle(config, device)
    task.screenshot()
    plan = task.battle_plan(preset=preset)
    if not plan:
        print("技能位一个都没认出来，先检查画面是不是停在技能栏上")
        return 1

    print("释放顺序：")
    for order, entry in enumerate(plan, start=1):
        print(f"  #{order} 技能位{entry.slot + 1}  {entry.name or '未识别'}"
              f"  {entry.skill_id or ''}")

    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        annotate(device.image, plan).save(args.save)
        print(f"标注图已保存: {args.save}")

    if args.list:
        return 0

    clicked = task.battle_attack(
        times=args.times,
        preset=preset["id"],
        plan=plan,
        interval=args.interval,
        respect_cooldown=False if args.no_cooldown else None,
        include_attack=not args.no_attack,
    )
    print(f"完成：{args.times} 轮，共点了 {clicked} 个技能")
    return 0


if __name__ == "__main__":
    sys.exit(main())
