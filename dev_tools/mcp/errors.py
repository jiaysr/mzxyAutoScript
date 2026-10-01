# This Python file uses the following encoding: utf-8
"""MCP 工具业务错误。"""


class McpToolError(Exception):
    """工具期望内的错误：转成 isError=true 的文本结果返回给调用方。"""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_text(self) -> str:
        if self.hint:
            return f"{self.message}\n提示：{self.hint}"
        return self.message
