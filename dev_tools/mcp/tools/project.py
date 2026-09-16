# This Python file uses the following encoding: utf-8
"""项目信息、配置与任务概览。"""
import json
import socket
import subprocess
import sys
from pathlib import Path

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import (
    ADB_EXECUTABLE,
    CONFIG_ROOT,
    OAS_SERVER_HOST,
    OAS_SERVER_PORT,
    PROJECT_ROOT,
    TASKS_ROOT,
    runtime,
)
from dev_tools.mcp.server import ToolRegistry, require_str, schema_object


def _oas_server_online() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        return sock.connect_ex((OAS_SERVER_HOST, OAS_SERVER_PORT)) == 0
    finally:
        sock.close()


def _adb_devices() -> str:
    if not ADB_EXECUTABLE.exists():
        return "未找到 adb.exe"
    try:
        completed = subprocess.run(
            [str(ADB_EXECUTABLE), "devices"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return f"adb 执行失败: {exc}"
    devices = [
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if line.strip() and not line.startswith("List of")
    ]
    return "; ".join(devices) if devices else "无设备"


def _read_config_device(config_file: Path) -> str:
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"(解析失败: {exc})"
    device = ((data.get("script") or {}).get("device") or {})
    handle = str(device.get("handle") or "").strip()
    serial = str(device.get("serial") or "").strip()
    return f"handle={handle or '-'} serial={serial or '-'}"


def get_project_info(args: dict) -> str:
    configs = runtime.available_configs()
    lines = [
        f"项目根: {PROJECT_ROOT}",
        f"Python: {sys.version.split()[0]} ({sys.executable})",
        f"默认配置: {runtime.default_config or '(未选定，缺省用第一个)'}",
        f"可用配置: {', '.join(configs) or '(无)'}",
        f"OAS 服务({OAS_SERVER_HOST}:{OAS_SERVER_PORT}): {'在线' if _oas_server_online() else '离线'}",
        f"设备(adb devices): {_adb_devices()}",
    ]
    return "\n".join(lines)


def list_configs(args: dict) -> str:
    lines: list[str] = []
    for config_file in sorted(CONFIG_ROOT.glob("*.json")):
        name = config_file.stem
        tag = " (模板)" if name == "template" else ""
        lines.append(f"{name}{tag}: {_read_config_device(config_file)}")
    if not lines:
        return "config/ 下没有 json 配置"
    return "\n".join(lines)


def select_config(args: dict) -> str:
    name = require_str(args, "name")
    selected = runtime.set_default_config(name)
    return f"已选定默认配置: {selected}"


def list_tasks(args: dict) -> str:
    if not TASKS_ROOT.is_dir():
        raise McpToolError("tasks/ 目录不存在")
    lines: list[str] = []
    for task_dir in sorted(TASKS_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not task_dir.is_dir() or task_dir.name.startswith("__"):
            continue
        if task_dir.name == "Component":
            for sub in sorted(task_dir.iterdir(), key=lambda p: p.name.lower()):
                if sub.is_dir() and not sub.name.startswith("__"):
                    lines.append(f"Component/{sub.name}: (组件，不单独直跑)")
            continue
        files = []
        if (task_dir / "script_task.py").exists():
            files.append("script_task.py")
        if (task_dir / "config.py").exists():
            files.append("config.py")
        if (task_dir / "assets.py").exists():
            files.append("assets.py")
        res_dir = task_dir / "res"
        res_json = sorted(p.name for p in res_dir.glob("*.json")) if res_dir.is_dir() else []
        image_count = len(list(res_dir.glob("*.png"))) if res_dir.is_dir() else 0
        lines.append(
            f"{task_dir.name}: {', '.join(files) or '(不完整)'}"
            f" | res: {', '.join(res_json) or '-'} | png: {image_count}"
        )
    return "\n".join(lines) if lines else "tasks/ 下没有任务"


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "get_project_info",
        "查看项目根、Python、可用配置、OAS 服务与设备连接状态。",
        schema_object(),
    )(get_project_info)
    registry.tool(
        "list_configs",
        "列出 config/*.json 配置及其模拟器 handle。",
        schema_object(),
    )(list_configs)
    registry.tool(
        "select_config",
        "选定本次会话的默认配置，后续 screenshot/run_task 未指定时使用它。",
        schema_object({"name": {"type": "string"}}, ["name"]),
    )(select_config)
    registry.tool(
        "list_tasks",
        "列出所有任务及其目录文件、规则文件与素材数量。",
        schema_object(),
    )(list_tasks)
