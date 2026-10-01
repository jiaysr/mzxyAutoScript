# This Python file uses the following encoding: utf-8
"""stdio 传输层的 JSON-RPC 2.0 编解码。

只依赖标准库：不安装官方 mcp SDK（其要求 anyio>=4.9 / pydantic>=2.12，
会破坏项目锁定的 starlette 0.27 / pydantic 2.10 环境）。
"""
import json
from typing import Any

JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """带错误码的 JSON-RPC 错误，可直接转成 error 响应。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_response(self, request_id: Any) -> dict:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def result_response(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def encode_message(message: dict) -> bytes:
    """编码为一行 UTF-8 JSON，尾部带换行。"""
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def parse_message(line) -> dict:
    """把一行原始数据解析为消息 dict；失败抛 JsonRpcError。"""
    if isinstance(line, (bytes, bytearray)):
        text = line.decode("utf-8", errors="replace")
    else:
        text = str(line)
    text = text.strip()
    if not text:
        raise JsonRpcError(PARSE_ERROR, "Empty message")
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonRpcError(PARSE_ERROR, f"Invalid JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise JsonRpcError(INVALID_REQUEST, "Message must be a JSON object")
    return message
