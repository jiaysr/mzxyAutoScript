# This Python file uses the following encoding: utf-8
"""素材与规则工具：截图、区域 OCR、裁剪素材、写入规则、规则测试。"""
import time
from pathlib import Path

import cv2
import numpy as np

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import PROJECT_ROOT, SCREENSHOT_DIR
from dev_tools.mcp.server import (
    ToolRegistry,
    image_content,
    optional_bool,
    require_str,
    schema_object,
)

# 常量前缀与 dev_tools/assets_extract.py 保持一致
RULE_PREFIX = {"image": "I", "click": "C", "long_click": "L", "swipe": "S", "ocr": "O", "list": "L"}
OCR_MODES = ("Single", "Full", "Digit", "DigitCounter", "Duration", "Quantity")


def parse_region(region: str, shape) -> tuple:
    """解析 "x,y,w,h" 并校验在图像范围内。"""
    text = (region or "").strip()
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise McpToolError(f"region 必须是 x,y,w,h 格式: {region!r}")
    try:
        x, y, w, h = [int(round(float(p))) for p in parts]
    except ValueError as exc:
        raise McpToolError(f"region 必须是数字: {region!r}") from exc
    if w <= 0 or h <= 0:
        raise McpToolError("region 的宽高必须大于 0")
    height, width = shape[0], shape[1]
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise McpToolError(f"region 超出图像范围: {region}（图像 {width}x{height}）")
    return x, y, w, h


def save_frame(image_rgb: np.ndarray, path: Path) -> dict:
    """把 RGB 图像存为 PNG，并返回 MCP image content block。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise McpToolError(f"写入图片失败: {path}")
    return image_content(path)


def load_image(path: Path) -> np.ndarray:
    """读取图片并转成 RGB。"""
    raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if raw is None:
        raise McpToolError(f"图片读取失败: {path}")
    return cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)


def _capture(config_name: str, tag: str = "screenshot"):
    from dev_tools.mcp.tools import device as device_tools

    image = device_tools.take_screenshot(config_name)
    path = SCREENSHOT_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
    save_frame(image, path)
    return image, path


def _load_source_image(image_path: str, config_name: str):
    if image_path:
        path = Path(image_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / image_path
        if not path.is_file():
            raise McpToolError(f"图片不存在: {image_path}")
        return load_image(path), path
    image, path = _capture(config_name)
    return image, path


def _crop_and_save(source: np.ndarray, region: tuple, target: Path) -> str:
    x, y, w, h = region
    crop = source[y:y + h, x:x + w]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)):
        raise McpToolError(f"写入素材失败: {target}")
    rel = target.relative_to(PROJECT_ROOT).as_posix()
    return f"已保存素材 {rel}（{w}x{h}）"


def screenshot(args: dict) -> list:
    config_name = require_str(args, "config", required=False, default="")
    image, path = _capture(config_name)
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    height, width = image.shape[:2]
    text = f"已截图 {width}x{height}，保存于 {rel}"
    return [{"type": "text", "text": text}, image_content(path)]


def ocr_region(args: dict) -> str:
    region = require_str(args, "region")
    mode = require_str(args, "mode", required=False, default="Single")
    if mode not in OCR_MODES:
        raise McpToolError(f"不支持的 OCR mode: {mode}", hint=f"可选: {', '.join(OCR_MODES)}")
    config_name = require_str(args, "config", required=False, default="")
    image_path = require_str(args, "image_path", required=False, default="")

    image, _ = _load_source_image(image_path, config_name)
    x, y, w, h = parse_region(region, image.shape)
    crop = image[y:y + h, x:x + w]

    from module.atom.ocr import RuleOcr

    rule = RuleOcr(
        name="mcp_ocr",
        mode=mode,
        method="Default",
        roi=(0, 0, w, h),
        area=(0, 0, w, h),
        keyword="",
    )
    try:
        boxed = rule.detect_and_ocr(crop, logDisplay=False)
    except Exception as exc:  # noqa: BLE001
        raise McpToolError(f"OCR 失败: {exc}", hint="确认 OCR 模型可用（首次运行会加载模型）") from exc

    if not boxed:
        return f"区域 {region} 未识别到文字"
    lines = [f"区域 {region} 识别到 {len(boxed)} 段文字："]
    for item in boxed:
        box = np.array(item.box)
        bx = int(np.min(box[:, 0])) + x
        by = int(np.min(box[:, 1])) + y
        bw = int(np.max(box[:, 0]) - np.min(box[:, 0]))
        bh = int(np.max(box[:, 1]) - np.min(box[:, 1]))
        lines.append(f"- {item.ocr_text}（score={float(item.score):.3f}, roi={bx},{by},{bw},{bh}）")
    return "\n".join(lines)


def _annotator():
    """延迟导入并返回 (AnnotatorError, annotator_manager)。"""
    from module.server.tool import AnnotatorError, annotator_manager

    return AnnotatorError, annotator_manager


def _call_annotator(func, *args, **kwargs):
    error_cls, _ = _annotator()
    try:
        return func(*args, **kwargs)
    except error_cls as exc:
        raise McpToolError(f"{exc.code}: {exc.message}") from exc


def _resolve_task_root(task: str) -> Path:
    _, manager = _annotator()
    return _call_annotator(manager._resolve_task_root, task)


def _resolve_json_path(task: str, json_relpath: str) -> tuple:
    _, manager = _annotator()
    return _call_annotator(manager._resolve_json_path, task, json_relpath)


def _normalize_rules(rule_type: str, rules: list, list_meta: dict = None):
    _, manager = _annotator()
    normalizer = getattr(manager, f"_normalize_{rule_type}_rules")
    if rule_type == "list":
        return _call_annotator(normalizer, rules, list_meta)
    return _call_annotator(normalizer, rules)


def _write_json(target: Path, payload) -> None:
    import json

    from filelock import FileLock
    from module.config.atomicwrites import atomic_write

    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(f"{target}.lock")
    with lock:
        with atomic_write(target, overwrite=True, encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)


def _read_json(target: Path):
    import json

    if not target.exists():
        return None
    with open(target, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _regenerate_assets(task_root: Path, target_json: Path) -> tuple:
    """重生 assets.py，返回 (assets 相对路径, 错误信息)。"""
    from dev_tools.assets_extract import AssetsExtractor

    _, manager = _annotator()
    try:
        extract_root, assets_path = manager._resolve_assets_extract_target(task_root, target_json)
        AssetsExtractor(str(extract_root)).extract()
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)
    return assets_path.relative_to(PROJECT_ROOT).as_posix(), ""


def _constant_names(rule_type: str, names: list, list_name: str = "") -> list:
    prefix = RULE_PREFIX[rule_type]
    if rule_type == "list":
        return [f"{prefix}_{list_name.upper()}"]
    return [f"{prefix}_{name.upper()}" for name in names]


def crop_asset(args: dict) -> str:
    task = require_str(args, "task")
    item_name = require_str(args, "item_name")
    region = require_str(args, "region")
    description = require_str(args, "description", required=False, default="")
    image_path = require_str(args, "image_path", required=False, default="")
    overwrite = optional_bool(args, "overwrite", False)

    if not item_name.replace("_", "").isalnum():
        raise McpToolError("item_name 只能包含字母、数字与下划线")

    task_root = _resolve_task_root(task)
    target = (task_root / "res" / f"res_{item_name}.png").resolve()
    try:
        target.relative_to(task_root)
    except ValueError as exc:
        raise McpToolError(f"素材路径越界: {item_name}") from exc
    if target.exists() and not overwrite:
        raise McpToolError(f"素材已存在: {target.name}", hint="覆盖请传 overwrite=true")

    source, _ = _load_source_image(image_path, "")
    x, y, w, h = parse_region(region, source.shape)
    text = _crop_and_save(source, (x, y, w, h), target)

    snippet = (
        "可用的 image 规则（用于 add_rule）：\n"
        "{\n"
        f'  "itemName": "{item_name}",\n'
        f'  "imageName": "res_{item_name}.png",\n'
        f'  "roiFront": "{region}",\n'
        f'  "roiBack": "{region}",\n'
        '  "threshold": 0.8,\n'
        f'  "description": "{description}"\n'
        "}"
    )
    return f"{text}\n{snippet}"


def add_rule(args: dict) -> str:
    task = require_str(args, "task")
    json_relpath = require_str(args, "json_relpath")
    rule_type = require_str(args, "rule_type")
    if rule_type not in RULE_PREFIX:
        raise McpToolError(
            f"不支持的 rule_type: {rule_type}",
            hint=f"可选: {', '.join(RULE_PREFIX)}",
        )
    regenerate = optional_bool(args, "regenerate", True)

    task_root, target_json = _resolve_json_path(task, json_relpath)

    if rule_type == "list":
        incoming = args.get("rules")
        if not isinstance(incoming, list) or not incoming:
            raise McpToolError("list 规则需要 rules 数组（项列表）")
        list_meta = args.get("list_meta")
        if not isinstance(list_meta, dict):
            raise McpToolError("list 规则需要 list_meta 对象")
        payload = _normalize_rules("list", incoming, list_meta)
        _write_json(target_json, payload)
        names = [str(item.get("itemName", "")) for item in payload.get("list", [])]
        constants = _constant_names("list", names, str(payload.get("name", "")))
        rule_count = len(payload.get("list", []))
    else:
        rule = args.get("rule")
        if not isinstance(rule, dict):
            raise McpToolError("需要 rule 对象")
        existing = _read_json(target_json)
        if existing is None:
            existing = []
        if not isinstance(existing, list):
            raise McpToolError(f"目标 JSON 不是规则数组: {json_relpath}")
        merged: dict = {}
        for item in existing:
            if isinstance(item, dict) and item.get("itemName"):
                merged[str(item["itemName"])] = item
        merged[str(rule.get("itemName", ""))] = rule
        payload = _normalize_rules(rule_type, list(merged.values()), None)
        _write_json(target_json, payload)
        names = [str(item.get("itemName", "")) for item in payload]
        constants = _constant_names(rule_type, names)
        rule_count = len(payload)

    lines = [
        f"已写入 {target_json.relative_to(PROJECT_ROOT).as_posix()}（rule_type={rule_type}，共 {rule_count} 条）",
        f"常量名: {', '.join(constants)}",
    ]
    if regenerate:
        assets_file, error = _regenerate_assets(task_root, target_json)
        if error:
            lines.append(f"assets.py 重生失败: {error}")
        else:
            lines.append(f"已重生 {assets_file}")
    return "\n".join(lines)


def _detect_rule_type(data) -> str:
    if isinstance(data, dict) and "list" in data and "name" in data:
        return "list"
    if isinstance(data, list) and data:
        item = data[0]
        if not isinstance(item, dict):
            return "unknown"
        if "imageName" in item:
            return "image"
        if "keyword" in item:
            return "ocr"
        if "duration" in item:
            return "long_click"
        if "mode" in item:
            return "swipe"
        return "click"
    return "unknown"


def read_rule_file(args: dict) -> str:
    task = require_str(args, "task")
    json_relpath = require_str(args, "json_relpath")
    _, target_json = _resolve_json_path(task, json_relpath)
    if not target_json.exists():
        raise McpToolError(f"规则文件不存在: {json_relpath}")

    import json

    data = json.loads(target_json.read_text(encoding="utf-8"))
    lines = [f"{json_relpath}:"]
    if isinstance(data, dict) and "list" in data:
        name = str(data.get("name", ""))
        lines.append(
            f"- list(L_{name.upper()}): direction={data.get('direction')} type={data.get('type')}"
            f" roiBack={data.get('roiBack')} description={data.get('description', '')}"
        )
        for item in data.get("list", []):
            lines.append(
                f"  - {item.get('itemName')} roiFront={item.get('roiFront')}"
                f" description={item.get('description', '')}"
            )
        return "\n".join(lines)

    if not isinstance(data, list):
        raise McpToolError(f"无法识别的规则文件结构: {json_relpath}")
    rule_type = _detect_rule_type(data)
    constants = _constant_names(rule_type, [str(i.get("itemName", "")) for i in data])
    for item, constant in zip(data, constants):
        lines.append(
            f"- {constant} ({item.get('itemName')}): roiFront={item.get('roiFront')}"
            f" roiBack={item.get('roiBack')} description={item.get('description', '')}"
        )
    return "\n".join(lines)


def _parse_roi_tuple(value) -> tuple:
    if not value:
        return None
    parts = [p.strip() for p in str(value).split(",")]
    if len(parts) != 4:
        raise McpToolError(f"ROI 必须是 x,y,w,h: {value!r}")
    try:
        x, y, w, h = [int(round(float(p))) for p in parts]
    except ValueError as exc:
        raise McpToolError(f"ROI 必须是数字: {value!r}") from exc
    return (x, y, w, h)


def test_rule(args: dict) -> str:
    task = require_str(args, "task")
    json_relpath = require_str(args, "json_relpath")
    rule_type = require_str(args, "rule_type")
    rule = args.get("rule")
    if not isinstance(rule, dict):
        raise McpToolError("需要 rule 对象")
    image_path = require_str(args, "image_path", required=False, default="")
    source, source_path = _load_source_image(image_path, "")

    from dev_tools.assets_test import detect_image_detail, detect_ocr_detail

    _, manager = _annotator()

    if rule_type == "image":
        image_name = str(rule.get("imageName", "")).strip()
        if not image_name:
            raise McpToolError("image 规则缺少 imageName")
        template = _call_annotator(manager.get_rule_image_file, task, json_relpath, image_name)
        roi_front = _parse_roi_tuple(rule.get("roiFront", ""))
        roi_back = _parse_roi_tuple(rule.get("roiBack", "")) or roi_front
        from module.atom.image import RuleImage

        target = RuleImage(
            roi_front=roi_front,
            roi_back=roi_back,
            method=str(rule.get("method", "Template matching")),
            threshold=float(rule.get("threshold", 0.8)),
            file=str(template),
        )
        detail = detect_image_detail(str(source_path), target)
        return (
            f"matched={detail['matched']} similarity={detail['similarity']}\n"
            f"roiFront={detail['roiFront']} roiBack={detail['roiBack']} message={detail['message']}"
        )

    if rule_type == "ocr":
        roi = _parse_roi_tuple(rule.get("roiFront", "")) or (0, 0, source.shape[1], source.shape[0])
        from module.atom.ocr import RuleOcr

        target = RuleOcr(
            name=str(rule.get("itemName", "mcp_test")),
            mode=str(rule.get("mode", "Single")),
            method=str(rule.get("method", "Default")),
            roi=roi,
            area=roi,
            keyword=str(rule.get("keyword", "")),
        )
        detail = detect_ocr_detail(str(source_path), target)
        return (
            f"matched={detail['matched']} similarity={detail['similarity']} text={detail['text']!r}\n"
            f"roiFront={detail['roiFront']} message={detail['message']}"
        )

    raise McpToolError(f"暂不支持测试的 rule_type: {rule_type}", hint="支持 image、ocr")


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "screenshot",
        "截取当前模拟器画面（1280x720），返回图片与保存路径。",
        schema_object({"config": {"type": "string"}}),
    )(screenshot)
    registry.tool(
        "ocr_region",
        "对区域做 OCR（复用项目 OCR），返回文本与坐标；可传 image_path 用已有截图。",
        schema_object(
            {
                "region": {"type": "string", "description": "x,y,w,h"},
                "config": {"type": "string"},
                "image_path": {"type": "string"},
                "mode": {"type": "string", "default": "Single"},
            },
            ["region"],
        ),
    )(ocr_region)
    registry.tool(
        "crop_asset",
        "从截图裁剪素材到 tasks/<task>/res/res_<item_name>.png，返回可用规则片段。",
        schema_object(
            {
                "task": {"type": "string"},
                "item_name": {"type": "string"},
                "region": {"type": "string"},
                "description": {"type": "string"},
                "image_path": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["task", "item_name", "region"],
        ),
    )(crop_asset)
    registry.tool(
        "add_rule",
        "把规则写入任务 res 下的 JSON 并自动重生 assets.py，返回生成的常量名。",
        schema_object(
            {
                "task": {"type": "string"},
                "json_relpath": {"type": "string"},
                "rule_type": {
                    "type": "string",
                    "enum": ["image", "ocr", "click", "swipe", "long_click", "list"],
                },
                "rule": {"type": "object", "description": "单条规则（非 list）"},
                "rules": {"type": "array", "items": {"type": "object"}, "description": "list 项列表"},
                "list_meta": {"type": "object", "description": "list 元信息"},
                "regenerate": {"type": "boolean", "default": True},
            },
            ["task", "json_relpath", "rule_type"],
        ),
    )(add_rule)
    registry.tool(
        "read_rule_file",
        "读取并解析任务的规则 JSON，返回每条规则对应的 assets.py 常量名。",
        schema_object(
            {"task": {"type": "string"}, "json_relpath": {"type": "string"}},
            ["task", "json_relpath"],
        ),
    )(read_rule_file)
    registry.tool(
        "test_rule",
        "对当前截图（或指定图片）测试 image/ocr 规则，返回 matched/得分/位置。",
        schema_object(
            {
                "task": {"type": "string"},
                "json_relpath": {"type": "string"},
                "rule_type": {"type": "string", "enum": ["image", "ocr"]},
                "rule": {"type": "object"},
                "image_path": {"type": "string"},
            },
            ["task", "json_relpath", "rule_type", "rule"],
        ),
    )(test_rule)
