# This Python file uses the following encoding: utf-8
"""工具注册、参数校验、JSON-RPC 分发与 stdio 主循环。"""
import base64
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    JsonRpcError,
    encode_message,
    parse_message,
    result_response,
)

SERVER_NAME = "oas-mcp"
SERVER_VERSION = "0.1.0"


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any] = field(repr=False)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def tool(self, name: str, description: str, input_schema: dict):
        """装饰器：@registry.tool(name, description, schema)"""

        def decorator(func):
            self.register(ToolSpec(name, description, input_schema, func))
            return func

        return decorator

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def call(self, name: str, arguments: Any) -> dict:
        spec = self._tools.get(name)
        if spec is None:
            raise McpToolError(f"未知工具: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise McpToolError("arguments 必须是对象")
        return {"content": _normalize_result(spec.handler(arguments)), "isError": False}

    def dispatch(self, message: dict) -> Optional[dict]:
        method = message.get("method")
        request_id = message.get("id")
        is_notification = "id" not in message
        if not isinstance(method, str):
            if is_notification:
                return None
            raise JsonRpcError(INVALID_REQUEST, "缺少 method")

        if method == "initialize":
            if is_notification:
                return None
            raw_params = message.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
            requested = params.get("protocolVersion")
            return result_response(
                request_id,
                {
                    "protocolVersion": requested or DEFAULT_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )

        if method in ("notifications/initialized", "notifications/cancelled"):
            return None

        if method == "ping":
            return None if is_notification else result_response(request_id, {})

        if method == "tools/list":
            if is_notification:
                return None
            return result_response(request_id, {"tools": self.list_tools()})

        if method == "tools/call":
            if is_notification:
                return None
            raw_params = message.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
            name = params.get("name")
            arguments = params.get("arguments")
            try:
                result = self.call(str(name), arguments)
            except McpToolError as exc:
                result = {"content": [{"type": "text", "text": exc.to_text()}], "isError": True}
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                result = {
                    "content": [
                        {"type": "text", "text": f"工具执行异常: {type(exc).__name__}: {exc}"}
                    ],
                    "isError": True,
                }
            return result_response(request_id, result)

        if is_notification:
            return None
        raise JsonRpcError(METHOD_NOT_FOUND, f"未知方法: {method}")


def _normalize_result(result: Any) -> list[dict]:
    if isinstance(result, str):
        return [{"type": "text", "text": result}]
    if isinstance(result, list):
        return result
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]


def run_stdio(registry: ToolRegistry, stdin=None, stdout=None) -> None:
    """同步 stdio 主循环；stdin/stdout 用二进制流，便于测试注入。"""
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        try:
            message = parse_message(line)
        except JsonRpcError as exc:
            stdout.write(encode_message(exc.to_response(None)))
            stdout.flush()
            continue
        try:
            response = registry.dispatch(message)
        except JsonRpcError as exc:
            response = exc.to_response(message.get("id"))
        if response is not None:
            stdout.write(encode_message(response))
            stdout.flush()


def schema_object(properties: Optional[dict] = None, required: Optional[list] = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def require_str(args: dict, key: str, *, required: bool = True, default: str = "") -> str:
    value = args.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise McpToolError(f"参数 {key} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise McpToolError(f"缺少参数: {key}")
    return value


def require_int(
    args: dict,
    key: str,
    *,
    required: bool = False,
    default: int = 0,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    value = args.get(key, default)
    if value is None or value == "":
        value = default
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise McpToolError(f"参数 {key} 必须是整数") from exc
    if minimum is not None and value < minimum:
        raise McpToolError(f"参数 {key} 不能小于 {minimum}")
    if maximum is not None and value > maximum:
        raise McpToolError(f"参数 {key} 不能大于 {maximum}")
    return value


def optional_bool(args: dict, key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no", ""):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise McpToolError(f"参数 {key} 必须是布尔值")


def image_content(path: Path) -> dict:
    data = Path(path).read_bytes()
    return {
        "type": "image",
        "data": base64.b64encode(data).decode("ascii"),
        "mimeType": "image/png",
    }


def main(stdout=None) -> None:
    from dev_tools.mcp.runtime import runtime
    from dev_tools.mcp.tools import register_all

    runtime.ensure_dirs()
    registry = ToolRegistry()
    register_all(registry)
    try:
        run_stdio(registry, stdout=stdout)
    finally:
        from dev_tools.mcp.tools import runner as runner_tools

        runner_tools.manager.stop_all()
