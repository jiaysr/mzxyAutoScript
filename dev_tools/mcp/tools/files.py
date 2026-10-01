# This Python file uses the following encoding: utf-8
"""文件读写、检索与路径安全。"""
import os
import re
import shutil
from pathlib import Path

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import PROJECT_ROOT
from dev_tools.mcp.server import (
    ToolRegistry,
    optional_bool,
    require_int,
    require_str,
    schema_object,
)

# 列目录时默认隐藏
HIDDEN_DIRS = {".git", "toolkit", "__pycache__", ".idea", ".vscode", ".github", "log"}
# 只读保护：禁止写入/删除
PROTECTED_PARTS = {".git", "toolkit", "__pycache__"}
# 删除白名单（项目根相对路径前缀）
DELETE_ALLOWED_PREFIXES = ("tasks/", "docs/mcp/", "tests/", "log/mcp/")
TEXT_SUFFIXES = {
    ".py", ".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".js", ".ts", ".html", ".css", ".bat", ".sh", ".log", ".lua",
}


def resolve_path(rel_path: str, *, must_exist: bool = False) -> Path:
    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw:
        raise McpToolError("路径不能为空")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise McpToolError("只接受项目根目录下的相对路径", hint=f"项目根: {PROJECT_ROOT}")
    target = (PROJECT_ROOT / raw).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise McpToolError(f"路径越界: {rel_path}") from exc
    if must_exist and not target.exists():
        raise McpToolError(f"路径不存在: {rel_path}")
    return target


def ensure_writable(target: Path) -> None:
    target = target.resolve()
    try:
        rel = target.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise McpToolError(f"路径越界: {target}") from exc
    for part in rel.parts:
        if part in PROTECTED_PARTS:
            raise McpToolError(f"禁止写入受保护目录: {part}")


def ensure_deletable(target: Path) -> None:
    ensure_writable(target)
    rel = target.resolve().relative_to(PROJECT_ROOT).as_posix()
    if not any(rel.startswith(prefix) for prefix in DELETE_ALLOWED_PREFIXES):
        raise McpToolError(
            f"该路径不允许删除: {rel}",
            hint=f"允许删除的目录: {', '.join(p.rstrip('/') for p in DELETE_ALLOWED_PREFIXES)}",
        )


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".mcp_tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def _iter_entries(base: Path, *, depth: int, include_log: bool):
    """产出 (路径, 是否目录, 深度)，目录优先，名称排序。"""

    def walk(current: Path, level: int):
        if level > depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if entry.name in HIDDEN_DIRS and not (include_log and entry.name == "log"):
                    continue
                if entry.name == "log" and not include_log:
                    continue
                yield entry, True, level
                yield from walk(entry, level + 1)
            else:
                yield entry, False, level

    yield from walk(base, 1)


def list_files(args: dict) -> str:
    rel = require_str(args, "path", required=False, default="")
    depth = require_int(args, "depth", default=2, minimum=1, maximum=6)
    include_log = optional_bool(args, "include_log", False)
    base = resolve_path(rel, must_exist=True) if rel else PROJECT_ROOT
    if not base.is_dir():
        raise McpToolError(f"不是目录: {rel or '.'}")

    lines: list[str] = []
    for entry, is_dir, level in _iter_entries(base, depth=depth, include_log=include_log):
        rel_entry = entry.relative_to(PROJECT_ROOT).as_posix()
        indent = "  " * (level - 1)
        lines.append(f"{indent}{rel_entry}{'/' if is_dir else ''}")
        if len(lines) >= 500:
            lines.append("... (已截断，缩小 depth 或指定 path)")
            break
    if not lines:
        return f"{rel or '.'} 下没有可见内容"
    return "\n".join(lines)


def read_file(args: dict) -> str:
    rel = require_str(args, "path")
    max_bytes = require_int(args, "max_bytes", default=200000, minimum=1)
    target = resolve_path(rel, must_exist=True)
    if target.is_dir():
        raise McpToolError(f"这是一个目录，不能读取: {rel}")
    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise McpToolError(f"无法解码文件（非文本文件？）: {rel}", hint="二进制文件请用其他工具处理")
    if truncated:
        text += f"\n\n... (文件超过 {max_bytes} 字节，已截断)"
    return text


def write_file(args: dict) -> str:
    rel = require_str(args, "path")
    content = args.get("content")
    if not isinstance(content, str):
        raise McpToolError("参数 content 必须是字符串")
    target = resolve_path(rel)
    ensure_writable(target)
    if target.is_dir():
        raise McpToolError(f"这是一个目录，不能写入: {rel}")
    _atomic_write_text(target, content)
    return (
        f"已写入 {target.relative_to(PROJECT_ROOT).as_posix()}"
        f"（{len(content.encode('utf-8'))} 字节，{content.count(chr(10)) + 1} 行）"
    )


def delete_file(args: dict) -> str:
    rel = require_str(args, "path")
    target = resolve_path(rel, must_exist=True)
    ensure_deletable(target)
    if target.is_dir():
        shutil.rmtree(target)
        return f"已删除目录 {rel}"
    target.unlink()
    return f"已删除文件 {rel}"


def search_code(args: dict) -> str:
    query = require_str(args, "query")
    scope = args.get("scope") or ["tasks"]
    if isinstance(scope, str):
        scope = [scope]
    if not isinstance(scope, list) or not scope:
        raise McpToolError("参数 scope 必须是字符串数组")
    max_results = require_int(args, "max_results", default=50, minimum=1, maximum=500)
    include_suffixes = args.get("suffixes")
    if include_suffixes is not None and not isinstance(include_suffixes, list):
        raise McpToolError("参数 suffixes 必须是字符串数组")

    try:
        pattern = re.compile(query)
    except re.error as exc:
        raise McpToolError(f"无效的正则表达式: {exc}") from exc

    suffix_filter = None
    if isinstance(include_suffixes, list):
        suffix_filter = {str(item).lower() for item in include_suffixes}

    results: list[str] = []
    for scope_rel in scope:
        base = resolve_path(str(scope_rel), must_exist=True)
        candidates = [base] if base.is_file() else list(base.rglob("*"))
        for path in candidates:
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if suffix_filter is not None and path.suffix.lower() not in suffix_filter:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    rel_path = path.relative_to(PROJECT_ROOT).as_posix()
                    results.append(f"{rel_path}:{line_no}: {line.strip()[:200]}")
                    if len(results) >= max_results:
                        return "\n".join(results) + f"\n... (达到上限 {max_results})"
    if not results:
        return f"未找到匹配: {query}"
    return "\n".join(results)


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "list_files",
        "列出项目内文件树。默认隐藏 .git/toolkit/__pycache__/log 等目录；path 为项目根相对路径。",
        schema_object(
            {
                "path": {"type": "string", "description": "项目根相对目录，默认项目根"},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 6},
                "include_log": {"type": "boolean", "default": False},
            }
        ),
    )(list_files)
    registry.tool(
        "read_file",
        "读取项目内文本文件（UTF-8，回退 GBK）。path 为项目根相对路径。",
        schema_object(
            {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "default": 200000},
            },
            ["path"],
        ),
    )(read_file)
    registry.tool(
        "write_file",
        "写入/覆盖项目内文本文件（原子写入，禁止写 .git/toolkit/__pycache__）。",
        schema_object(
            {"path": {"type": "string"}, "content": {"type": "string"}},
            ["path", "content"],
        ),
    )(write_file)
    registry.tool(
        "delete_file",
        "删除文件或目录，仅允许 tasks/、docs/mcp/、tests/、log/mcp/ 下。",
        schema_object({"path": {"type": "string"}}, ["path"]),
    )(delete_file)
    registry.tool(
        "search_code",
        "在指定项目子目录中做正则检索，返回 文件:行号:内容。",
        schema_object(
            {
                "query": {"type": "string"},
                "scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["tasks"],
                },
                "max_results": {"type": "integer", "default": 50},
                "suffixes": {"type": "array", "items": {"type": "string"}},
            },
            ["query"],
        ),
    )(search_code)
