# This Python file uses the following encoding: utf-8
"""技能栏实时监测：先认出 7 个技能位装的是什么技能，再实时看冷却状态。

用法（项目根目录执行，游戏切到能看到技能栏的画面/战斗中）：
    toolkit\\python.exe tests\\skill_cooldown_monitor.py
    toolkit\\python.exe tests\\skill_cooldown_monitor.py --interval 0.1 --port 22270
    toolkit\\python.exe tests\\skill_cooldown_monitor.py --image 截图.png   # 离线跑一张图（不连设备）

启动流程：
1. 连设备截一帧，用技能目录（tasks/Battle）识别 7 个技能位分别是什么技能；
2. 把 GameUi 里 7 张「冷却好」基准素材（res_skill<1-7>_ready.png）逐张匹配到技能目录，
   得到「技能 -> 基准分」的表；
3. 每个技能位的基准分 = 该位置识别出的技能对应的基准分：
   识别到但没素材的，自动改用「运行中观测到的最高分」兜底（面板显示「自动基准 ON」）；
   没识别出的，退回同序号的素材（日志里会注明是兜底）；
4. 之后逐帧只看冷却：分数 = 按钮中心圆盘（半径 = 短边 x 0.30）的
   「中位灰度 x 中位彩度 / 255」，当前分 < 基准分 x ratio（默认 0.7）判为冷却中；
5. 状态防抖（默认 2 帧）：冷却快结束时游戏会把冷却遮罩闪一下，分数会单帧跳到
   0.6~0.7（实测「铁骨」：正常冷却 0.01~0.1，尾巴上有单帧 0.64/0.69），
   不防抖就会闪出「冷却好」。`--stable 1` 可以关掉防抖。

为什么要有第 2、3 步：基准素材是「按技能位」存的，但换一套技能后同一个位置上的技能就变了，
拿旧素材比出来的比例没有意义。现在按「识别出的技能」去找对应素材，换技能也能用。

画面说明：
- 每个技能位画一个框：绿色 = 冷却好，红色 = 冷却中，黄色 = 无基准，框内左上角是技能序号，
  框底是识别出的技能名；
- 右侧面板逐个列出 技能序号 / 技能名 / 状态 / 当前分 / 基准分 / 比例；
- 页面地址 http://127.0.0.1:22270/ （自动打开浏览器），点「停止」或 Ctrl+C 退出。

判定算法和任务一致（tasks/Battle/cooldown.py 的 SkillCooldownJudge + SkillStateStabilizer），
但监控默认更严：
ratio 0.7（任务用 0.6 —— 自动化宁可早点点、不能漏点；监控是给人看的，显示准确更重要），
再加 2 帧防抖。想和任务完全对齐：`--ratio 0.6 --stable 1`。
加 --auto-ref 时基准分会按运行中观测到的最高分自动抬高（缺素材时兜底）。

参数：
    --list      只打印 7 个技能位区域、基准素材匹配到的技能，然后退出（不连设备）
    --image     离线模式：用一张 1280x720 截图跑（默认只处理 1 帧，可配 --save 存标注图）
    --threshold 技能识别相似度下限（默认 0.78）
    --preset    按哪个战斗预设打印释放顺序（默认系统预设，仅日志用）
"""
import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(PROJECT_ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.chdir(PROJECT_ROOT)

from live_mjpeg_viewer import FrameViewer

DEFAULT_RATIO = 0.7
DEFAULT_FLOOR = 0.0
DEFAULT_INTERVAL = 0.2
# 状态切换要连续这么多帧才算数（冷却结束前游戏会闪一下遮罩，单帧会跳到 0.6~0.7）
DEFAULT_STABLE = 2
DEFAULT_PORT = 22270
REPORT_INTERVAL = 1.0
PANEL_WIDTH = 470

STATE_READY = "ready"
STATE_COOLING = "cooling"
STATE_UNKNOWN = "unknown"

COLOR_READY = (70, 210, 70)
COLOR_COOLING = (240, 70, 60)
COLOR_UNKNOWN = (200, 200, 90)
COLOR_TEXT = (225, 225, 225)
COLOR_DIM = (165, 165, 165)
COLOR_NAME = (255, 255, 255)

# 基准素材匹配到技能目录时的最低相似度
REFERENCE_MATCH_THRESHOLD = 0.7

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)
_FONT_CACHE = {}


def cjk_font(size: int):
    """中文字体（PIL 默认字体不支持中文）"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for path in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[size] = font
            return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def draw_labels(image_rgb: np.ndarray, labels: list) -> np.ndarray:
    """labels: [(text, (x, y), (r, g, b), size)]"""
    if not labels:
        return image_rgb
    pil = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil)
    for text, (x, y), color, size in labels:
        font = cjk_font(size)
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        draw.rectangle([left - 3, top - 2, right + 3, bottom + 2], fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=tuple(color))
    return np.array(pil)


def skill_regions() -> dict:
    """7 个技能位的判定区域（来自战斗公共常量）"""
    from tasks.GameUi.navigation.battle import SimpleBattle
    return dict(SimpleBattle.BATTLE_SKILL_CHECK_REGIONS)


# ---------------------------------------------------------------- 技能识别
def skill_catalog():
    from tasks.Battle.catalog import load_catalog
    return load_catalog()


def identify_slots(frame_rgb: np.ndarray, catalog, threshold: float,
                   margin: float) -> dict:
    """识别 7 个技能位分别是什么技能 -> {slot: SlotMatch}"""
    from tasks.Battle.recognizer import SkillRecognizer
    from tasks.Battle.slots import BATTLE_SKILL_CHECK_REGIONS

    recognizer = SkillRecognizer.from_catalog(catalog, threshold=threshold, margin=margin)
    return recognizer.identify(frame_rgb, dict(BATTLE_SKILL_CHECK_REGIONS))


def slot_skill_names(matches: dict, catalog) -> dict:
    """{slot: 显示名}，未识别就显示「未识别」"""
    names = {}
    for slot, match in matches.items():
        skill = catalog.skill(match.skill_id) if match.skill_id else None
        names[slot] = skill.name if skill else "未识别"
    return names


def identify_and_print(frame_rgb: np.ndarray, catalog, threshold: float,
                       margin: float) -> dict:
    """识别一次并打印结果，返回 {slot: SlotMatch}"""
    matches = identify_slots(frame_rgb, catalog, threshold, margin)
    print("技能栏识别：")
    for slot in sorted(matches):
        match = matches[slot]
        skill = catalog.skill(match.skill_id) if match.skill_id else None
        if skill:
            print(f"  技能{slot + 1}: {skill.name}（{skill.id}，{catalog.class_name(skill.id)}）"
                  f"  分数 {match.score:.2f}  第二名 {match.second:.2f}")
        else:
            print(f"  技能{slot + 1}: 未识别  分数 {match.score:.2f}  第二名 {match.second:.2f}")
    return matches


# ---------------------------------------------------------------- 冷却基准
def load_reference_assets() -> dict:
    """从 GameUi 读 7 张「冷却好」基准素材 -> {slot: RGB 图}"""
    from tasks.GameUi.assets import GameUiAssets
    from tasks.GameUi.navigation.battle import SimpleBattle

    assets = {}
    for index, attr_name in SimpleBattle.BATTLE_SKILL_READY_ASSET.items():
        rule = getattr(GameUiAssets, attr_name, None)
        if rule is None:
            continue
        try:
            assets[index] = rule.image  # RGB
        except Exception as exc:  # noqa: BLE001
            print(f"技能{index + 1} 基准素材读取失败（{attr_name}）: {exc}")
    return assets


def match_references_to_skills(assets: dict, catalog) -> dict:
    """
    把每张「冷却好」素材匹配到技能目录里的技能

    换一套技能后，同一个技能位上的技能会变；按「素材对应的技能」去找基准分才靠谱，
    所以启动时先建这张 {技能 id: 素材图} 的表。

    :return: ({技能 id: 素材图}, {素材序号: 技能 id})，匹配不上的不进结果
    """
    from tasks.Battle.recognizer import load_icon, similarity
    from tasks.Battle.slots import SKILL_ICON_INSET

    icon_x, icon_y, icon_w, icon_h = SKILL_ICON_INSET
    icons = {}
    for skill in catalog.skills:
        path = catalog.icon_path(skill.id)
        if path is None:
            continue
        try:
            icons[skill.id] = load_icon(path)[0]
        except Exception as exc:  # noqa: BLE001
            print(f"技能 {skill.id} 图标读取失败: {exc}")

    matched, slot_to_skill = {}, {}
    for slot, image in sorted(assets.items()):
        inner = image[icon_y:icon_y + icon_h, icon_x:icon_x + icon_w]
        best_id, best_score = None, -1.0
        for skill_id, icon in icons.items():
            score = similarity(inner, icon)
            if score > best_score:
                best_id, best_score = skill_id, score
        if best_id is not None and best_score >= REFERENCE_MATCH_THRESHOLD:
            matched[best_id] = image
            slot_to_skill[slot] = best_id
            print(f"  素材 技能{slot + 1} -> {catalog.class_name(best_id)} {best_id}"
                  f"（相似度 {best_score:.2f}）")
        else:
            print(f"  素材 技能{slot + 1} 没匹配到技能目录里的图标"
                  f"（最像 {best_id}，相似度 {best_score:.2f}），按原序号兜底")
    return matched, slot_to_skill


def build_slot_references(assets: dict, assets_by_skill: dict, matches: dict) -> tuple:
    """
    每个技能位用哪张「冷却好」素材

    - 识别出技能、且这个技能有素材 -> 用它的素材（换技能也能用）
    - 识别出技能、但没素材 -> 不给基准（显示「无基准」），比拿别的技能的素材硬算靠谱
    - 没识别出技能 -> 退回同序号素材（只能赌一把，日志里会说明）

    :return: ({slot: RGB 图}, {slot: 说明})
    """
    resolved, sources = {}, {}
    for slot, asset in sorted(assets.items()):
        match = matches.get(slot)
        if match is not None and match.skill_id:
            skill_asset = assets_by_skill.get(match.skill_id)
            if skill_asset is not None:
                resolved[slot] = skill_asset
                sources[slot] = f"技能 {match.skill_id} 的素材"
            else:
                sources[slot] = f"技能 {match.skill_id} 没有「冷却好」素材"
        else:
            resolved[slot] = asset
            sources[slot] = "未识别，按同序号素材兜底"
    return resolved, sources


def score_references(assets: dict) -> dict:
    """基准素材 -> 基准分"""
    from tasks.Battle.cooldown import skill_region_score

    references = {}
    for index, image in assets.items():
        height, width = image.shape[:2]
        references[index] = skill_region_score(image, (0, 0, width, height))
    return references


# ---------------------------------------------------------------- 冷却判定
def evaluate_skills(frame_rgb: np.ndarray, regions: dict, references: dict,
                    ratio: float, floor: float, auto_ref: bool = False) -> list:
    """逐技能打分并判定状态，返回 [dict(index, score, ref, ratio, state)]"""
    from tasks.Battle.cooldown import skill_region_score

    rows = []
    for index in sorted(regions):
        score = skill_region_score(frame_rgb, regions[index])
        if auto_ref and score > references.get(index, 0.0):
            references[index] = score
        reference = references.get(index, 0.0)
        if reference <= 0:
            state = STATE_UNKNOWN
            current_ratio = 0.0
        else:
            current_ratio = score / reference
            state = STATE_COOLING if score < max(reference * ratio, floor) else STATE_READY
        rows.append({"index": index, "score": score, "ref": reference,
                     "ratio": current_ratio, "state": state})
    return rows


def state_color(state: str) -> tuple:
    if state == STATE_READY:
        return COLOR_READY
    if state == STATE_COOLING:
        return COLOR_COOLING
    return COLOR_UNKNOWN


def state_text(state: str) -> str:
    return {STATE_READY: "冷却好", STATE_COOLING: "冷却中", STATE_UNKNOWN: "无基准"}[state]


def build_display(frame_rgb: np.ndarray, regions: dict, rows: list, meta: dict,
                  names: dict) -> np.ndarray:
    """左：实时画面 + 7 个技能框；右：状态面板"""
    view = frame_rgb.copy()
    name_labels = []
    for row in rows:
        index = row["index"]
        x, y, w, h = regions[index]
        color = state_color(row["state"])
        cv2.rectangle(view, (x, y), (x + w - 1, y + h - 1), color, 2)
        # 序号小徽标（框内左上角），状态看框颜色/右侧面板
        cv2.rectangle(view, (x, y), (x + 17, y + 17), color, -1)
        cv2.putText(view, str(index + 1), (x + 3, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
        # 技能名（框底），没识别出来就不画
        name = names.get(index, "")
        if name and name != "未识别":
            name_labels.append((name, (x + 1, y + h - 15), COLOR_NAME, 12))
    view = draw_labels(view, name_labels)

    known = sum(1 for row in rows if row["state"] != STATE_UNKNOWN)
    panel = np.full((view.shape[0], PANEL_WIDTH, 3), 28, np.uint8)
    labels = [
        ("技能栏识别 + 冷却监测", (view.shape[1] + 16, 16), COLOR_TEXT, 22),
        ("绿=冷却好  红=冷却中  黄=无基准", (view.shape[1] + 16, 50), COLOR_DIM, 15),
    ]
    y = 88
    for row in rows:
        color = state_color(row["state"])
        name = names.get(row["index"], "未识别")
        labels.append((f"技能{row['index'] + 1}  {name}",
                       (view.shape[1] + 16, y), COLOR_TEXT, 19))
        labels.append((f"{state_text(row['state'])}   分数 {row['score']:6.2f}   "
                       f"基准 {row['ref']:6.2f}   比例 {row['ratio']:.2f}",
                       (view.shape[1] + 16, y + 24), color, 14))
        y += 54

    y += 8
    labels += [
        (f"fps {meta['fps']:.1f}   截图+判定 {meta['cost'] * 1000:.0f} ms   帧 {meta['frames']}",
         (view.shape[1] + 16, y), (160, 200, 255), 14),
        (f"ratio {meta['ratio']:.2f}   floor {meta['floor']:.2f}   "
         f"稳定 {meta['stable']} 帧"
         + ("   自动基准 ON" if meta["auto_ref"] else ""),
         (view.shape[1] + 16, y + 22), COLOR_DIM, 14),
        (f"统计：{meta['ready']} 好 / {meta['cooling']} 冷却 / {meta['unknown']} 无基准"
         f"   有基准的技能位 {known}/{len(rows)}",
         (view.shape[1] + 16, y + 44), COLOR_DIM, 14),
    ]
    return draw_labels(np.hstack([view, panel]), labels)


def capture_frame(device, method: str) -> np.ndarray:
    if method == "auto":
        return device.screenshot()
    if method == "emulator_extras":
        return device.screenshot_emulator_extras()
    if method == "droidcast_raw":
        return device.screenshot_droidcast_raw()
    return device.screenshot_adb()


def parse_args():
    parser = argparse.ArgumentParser(description="技能栏识别 + 7 个技能位冷却实时监测（浏览器标注）")
    parser.add_argument("--config", default="oas1", help="config/<name>.json 中的配置名")
    parser.add_argument("--method", choices=("auto", "emulator_extras", "droidcast_raw", "adb"),
                        default="auto", help="截图方式，auto=按配置")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="截图间隔（秒）")
    parser.add_argument("--ratio", type=float, default=DEFAULT_RATIO,
                        help="当前分 < 基准分 x ratio 判为冷却中（默认 0.7）")
    parser.add_argument("--stable", type=int, default=DEFAULT_STABLE,
                        help="状态要连续多少帧才切换（默认 2；设 1 关闭防抖）")
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR,
                        help="冷却判定绝对下限分（0=不用，自动基准时兜底用）")
    parser.add_argument("--auto-ref", action="store_true",
                        help="基准分按运行中观测到的最高分自动抬高（缺素材时用）")
    parser.add_argument("--threshold", type=float, default=None,
                        help="技能识别相似度下限（默认用配置里的 identify_threshold）")
    parser.add_argument("--margin", type=float, default=None,
                        help="技能识别「与第二名的差距」下限（默认 0.05）")
    parser.add_argument("--preset", default="", help="打印哪个战斗预设的释放顺序（默认系统预设）")
    parser.add_argument("--frames", type=int, default=0, help="处理帧数，0 表示一直运行")
    parser.add_argument("--image", type=Path, default=None,
                        help="离线模式：用这张 1280x720 截图跑（不连设备，默认只处理 1 帧）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="浏览器查看端口，0 表示随机")
    parser.add_argument("--no-viewer", action="store_true", help="不启动浏览器查看器（只打印结果）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--save", type=Path, default=None, help="退出时保存最后一帧标注图")
    parser.add_argument("--list", action="store_true",
                        help="打印 7 个技能位区域、基准素材匹配到的技能后退出（不连设备）")
    return parser.parse_args()


def read_identify_settings(config_name: str, args):
    """识别阈值默认取配置（battle.common.identify_threshold）"""
    from tasks.Battle.config import Battle
    from tasks.Battle.recognizer import DEFAULT_MARGIN, DEFAULT_THRESHOLD

    threshold, margin = DEFAULT_THRESHOLD, DEFAULT_MARGIN
    try:
        from module.config.config import Config
        battle = getattr(Config(config_name), 'battle', None)
        if battle is not None:
            threshold = float(battle.common.identify_threshold)
    except Exception as exc:  # noqa: BLE001
        print(f"读取配置 {config_name} 失败（用默认阈值 {threshold:.2f}）：{exc}")
    if args.threshold is not None:
        threshold = args.threshold
    if args.margin is not None:
        margin = args.margin
    return threshold, margin


def print_preset_plan(matches: dict, catalog, preset_id: str,
                      config_name: str = 'oas1') -> None:
    """打印按预设算出来的释放顺序（和开战时一致），仅日志用"""
    try:
        from tasks.Battle.config import DEFAULT_PRESET_ID, Battle
        from tasks.Battle.planner import build_plan, plan_summary
        from tasks.Battle.presets import resolve_preset
        from module.config.config import Config

        battle = getattr(Config(config_name), 'battle', None) or Battle()
        preset = resolve_preset(battle, preset_id or DEFAULT_PRESET_ID, catalog)
        order = catalog.normalize_order(list(preset['order']), list(preset['disabled']))
        plan = build_plan(matches, order, catalog, disabled=list(preset['disabled']))
        print(f"预设 {preset['name']} 的释放顺序：{plan_summary(plan)}")
    except Exception as exc:  # noqa: BLE001
        print(f"打印释放顺序失败：{exc}")


def main():
    args = parse_args()

    regions = skill_regions()
    catalog = skill_catalog()
    assets = load_reference_assets()

    print(f"技能位 {len(regions)} 个，基准素材 {len(assets)} 张，"
          f"技能目录 {len(catalog.skills)} 个技能")
    print("基准素材匹配到技能：")
    assets_by_skill, slot_to_skill = match_references_to_skills(assets, catalog)
    for index in sorted(regions):
        print(f"  技能{index + 1}: 区域 {regions[index]}  "
              f"素材 -> {slot_to_skill.get(index, '未匹配')}")
    if args.list:
        return

    if args.save is not None and args.save.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp'):
        print(f"--save 需要图片后缀（.png/.jpg），已忽略: {args.save}")
        args.save = None

    device = None
    image_frame = None
    if args.image is not None:
        source = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
        if source is None:
            raise SystemExit(f"图片读取失败: {args.image}")
        image_frame = cv2.cvtColor(source, cv2.COLOR_BGR2RGB)
        args.frames = args.frames or 1
        print(f"离线模式: {args.image}（{image_frame.shape[1]}x{image_frame.shape[0]}）")
    else:
        from module.device.screenshot import Screenshot
        device = Screenshot(config=args.config)
        print(f"设备: {device.serial}, 包名: {device.package}, 截图方式: {args.method}")

    threshold, margin = read_identify_settings(args.config, args)
    print(f"技能识别阈值 {threshold:.2f}，margin {margin:.2f}")

    # 先识别技能栏：拿一帧识别，再按识别结果挑每个位置该用哪张基准素材
    method = args.method
    if image_frame is not None:
        frame = image_frame
    else:
        try:
            frame = capture_frame(device, method)
        except Exception as exc:  # noqa: BLE001
            print(f"首帧截图失败: {exc}，回退到 adb")
            method = "adb"
            frame = capture_frame(device, method)

    matches = identify_and_print(frame, catalog, threshold, margin)
    names = slot_skill_names(matches, catalog)

    slot_assets, reference_sources = build_slot_references(assets, assets_by_skill, matches)
    references = score_references(slot_assets)
    print("各技能位使用的基准素材：")
    for slot in sorted(regions):
        print(f"  技能{slot + 1}（{names.get(slot, '未识别')}）: "
              f"{reference_sources.get(slot, '无')}  基准分 {references.get(slot, 0.0):.2f}")

    missing = [slot for slot in sorted(regions) if references.get(slot, 0.0) <= 0]
    auto_ref = args.auto_ref
    if missing and not auto_ref:
        # 没有素材的技能位：自动改用「运行中观测到的最高分」当基准，否则这几个位置永远是「无基准」
        auto_ref = True
        print(f"技能位 {[slot + 1 for slot in missing]} 没有「冷却好」素材，"
              f"自动开启自动基准（用运行中观测到的最高分兜底，换技能也能量出相对变化）")
    if missing:
        print("想用固定基准的话：截一张该技能「冷却好」的 52x54 素材放进 tasks/GameUi/res/battle/，"
              "登记到 SimpleBattle.BATTLE_SKILL_READY_ASSET 后重跑")

    print_preset_plan(matches, catalog, args.preset, args.config)

    print(f"判定: 当前分 < max(基准 x {args.ratio}, {args.floor}) -> 冷却中"
          f"{'（自动基准）' if auto_ref else ''}")

    viewer = None
    if not args.no_viewer:
        kwargs = dict(
            title="OAS 技能栏识别 + 冷却监测",
            subtitle="（绿=冷却好，红=冷却中，黄=无基准；框内数字=技能位，框底=技能名）",
        )
        try:
            viewer = FrameViewer(port=args.port, **kwargs)
        except OSError:
            viewer = FrameViewer(port=0, **kwargs)
        url = viewer.start()
        print(f"实时标注页面: {url}")
        if not args.no_browser:
            webbrowser.open(url)

    frames = 0
    fps = 0.0
    display = None
    last_report = 0.0
    last_states = {}
    from tasks.Battle.cooldown import SkillStateStabilizer

    stabilizer = SkillStateStabilizer(args.stable)
    try:
        while True:
            if viewer is not None and viewer.stop_event.is_set():
                print("收到停止请求，退出")
                break

            frame_start = time.time()
            if image_frame is not None:
                frame = image_frame
            else:
                try:
                    frame = capture_frame(device, method)
                except Exception as exc:  # noqa: BLE001
                    if method != "adb":
                        print(f"{method} 截图失败: {exc}，回退到 adb")
                        method = "adb"
                        continue
                    print(f"截图失败: {exc}")
                    time.sleep(1)
                    continue

            height, width = frame.shape[:2]
            for index, (x, y, w, h) in regions.items():
                if x + w > width or y + h > height:
                    raise SystemExit(f"技能{index + 1} 区域 {regions[index]} 超出画面 {width}x{height}")

            rows = evaluate_skills(frame, regions, references, args.ratio, args.floor,
                                   auto_ref=auto_ref)
            raw_states = {row["index"]: row["state"] for row in rows}
            stable_states = stabilizer.update(raw_states)
            for row in rows:
                row["raw_state"] = row["state"]
                row["state"] = stable_states[row["index"]]
            frames += 1
            cost = time.time() - frame_start
            fps = 1 / cost if cost > 0 else 0.0

            now = time.time()
            states = {row["index"]: row["state"] for row in rows}
            changed = {i: s for i, s in states.items()
                       if i in last_states and last_states[i] != s}
            last_states = states
            for index, state in changed.items():
                row = rows[index]
                print(f"技能{index + 1}（{names.get(index, '未识别')}）-> {state_text(state)} "
                      f"(分数 {row['score']:.2f} / 基准 {row['ref']:.2f} = {row['ratio']:.2f})")
            if now - last_report >= REPORT_INTERVAL:
                last_report = now
                brief = " ".join(f"{row['index'] + 1}:{names.get(row['index'], '')}"
                                 f"{state_text(row['state'])}({row['ratio']:.2f})"
                                 for row in rows)
                print(f"[{time.strftime('%H:%M:%S')}] {brief}")

            ready = sum(1 for row in rows if row["state"] == STATE_READY)
            cooling = sum(1 for row in rows if row["state"] == STATE_COOLING)
            display = build_display(frame, regions, rows, {
                "fps": fps, "cost": cost, "frames": frames,
                "ratio": args.ratio, "floor": args.floor, "auto_ref": auto_ref,
                "stable": args.stable,
                "ready": ready, "cooling": cooling, "unknown": len(rows) - ready - cooling,
            }, names)

            if viewer is not None:
                ok, buffer = cv2.imencode(
                    ".jpg", cv2.cvtColor(display, cv2.COLOR_RGB2BGR),
                    [int(cv2.IMWRITE_JPEG_QUALITY), 85],
                )
                if ok:
                    viewer.update(buffer.tobytes())

            if args.frames and frames >= args.frames:
                break

            remain = args.interval - (time.time() - frame_start)
            if remain > 0:
                time.sleep(remain)
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        if viewer is not None:
            viewer.stop()
        if args.save and display is not None:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.save), cv2.cvtColor(display, cv2.COLOR_RGB2BGR))
            print(f"标注图已保存: {args.save}")


if __name__ == "__main__":
    main()
