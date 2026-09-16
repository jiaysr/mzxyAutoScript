# This Python file uses the following encoding: utf-8
"""MCP 知识库：docs/mcp 下的规范文档。"""
from pathlib import Path

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import DOCS_ROOT
from dev_tools.mcp.server import ToolRegistry, require_str, schema_object


def _doc_files() -> list[Path]:
    if not DOCS_ROOT.is_dir():
        return []
    return sorted(DOCS_ROOT.glob("*.md"), key=lambda p: p.name.lower())


def list_docs(args: dict) -> str:
    files = _doc_files()
    if not files:
        return "docs/mcp 下暂无文档"
    lines: list[str] = []
    for path in files:
        title = ""
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    title = line.lstrip("#").strip()
                    break
        except OSError:
            pass
        lines.append(f"{path.name}: {title or '(无标题)'}")
    return "\n".join(lines)


def read_doc(args: dict) -> str:
    name = require_str(args, "name")
    if "/" in name or "\\" in name:
        raise McpToolError("name 只能是 docs/mcp 下的文件名")
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    target = (DOCS_ROOT / name).resolve()
    try:
        target.relative_to(DOCS_ROOT.resolve())
    except ValueError as exc:
        raise McpToolError(f"路径越界: {name}") from exc
    if not target.is_file():
        raise McpToolError(
            f"文档不存在: {name}",
            hint=f"可用文档: {', '.join(p.name for p in _doc_files()) or '(无)'}",
        )
    return target.read_text(encoding="utf-8")


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "list_docs",
        "列出 docs/mcp 下的任务开发文档。写任务代码前建议先读规范。",
        schema_object(),
    )(list_docs)
    registry.tool(
        "read_doc",
        "读取 docs/mcp 下的文档全文（name 可省略 .md）。",
        schema_object({"name": {"type": "string"}}, ["name"]),
    )(read_doc)
