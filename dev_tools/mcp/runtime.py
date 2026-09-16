# This Python file uses the following encoding: utf-8
"""MCP 运行期常量与会话状态。"""
from pathlib import Path

from dev_tools.mcp.errors import McpToolError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASKS_ROOT = PROJECT_ROOT / "tasks"
CONFIG_ROOT = PROJECT_ROOT / "config"
LOG_ROOT = PROJECT_ROOT / "log"
MCP_LOG_ROOT = LOG_ROOT / "mcp"
SCREENSHOT_DIR = MCP_LOG_ROOT / "screenshots"
RUN_LOG_DIR = MCP_LOG_ROOT / "runs"
ERROR_LOG_ROOT = LOG_ROOT / "error"
DOCS_ROOT = PROJECT_ROOT / "docs" / "mcp"
PYTHON_EXECUTABLE = PROJECT_ROOT / "toolkit" / "python.exe"
ADB_EXECUTABLE = (
    PROJECT_ROOT / "toolkit" / "Lib" / "site-packages" / "adbutils" / "binaries" / "adb.exe"
)

OAS_SERVER_HOST = "127.0.0.1"
OAS_SERVER_PORT = 22267


class Runtime:
    """单进程会话状态（MCP 进程生命周期内有效）。"""

    def __init__(self) -> None:
        self.default_config: str = ""

    def ensure_dirs(self) -> None:
        for path in (MCP_LOG_ROOT, SCREENSHOT_DIR, RUN_LOG_DIR):
            path.mkdir(parents=True, exist_ok=True)

    def available_configs(self) -> list[str]:
        from module.server.config_manager import ConfigManager

        return ConfigManager.all_script_files()

    def resolve_config(self, name: str = "") -> str:
        name = (name or self.default_config or "").strip()
        configs = self.available_configs()
        if not name:
            if not configs:
                raise McpToolError("没有可用的配置")
            return configs[0]
        if name not in configs:
            raise McpToolError(
                f"配置不存在: {name}", hint=f"可用配置: {', '.join(configs)}"
            )
        return name

    def set_default_config(self, name: str) -> str:
        self.default_config = self.resolve_config(name)
        return self.default_config


runtime = Runtime()
