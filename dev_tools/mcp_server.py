# This Python file uses the following encoding: utf-8
"""OAS MCP 服务入口（stdio）。

opencode 以本地 MCP 子进程方式启动：
    toolkit\\python.exe dev_tools/mcp_server.py

注意：项目 logger 在导入时会把 rich Console 绑到当时的 sys.stdout，
所以必须先重定向 stdout，再导入任何项目模块，协议流走真正的 stdout。
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _prepare() -> object:
    real_stdout = sys.stdout
    if sys.stderr is not None:
        sys.stdout = sys.stderr
    else:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    script_dir = PROJECT_ROOT / "dev_tools"
    if sys.path and Path(sys.path[0]).resolve() == script_dir.resolve():
        sys.path[0] = str(PROJECT_ROOT)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    os.chdir(PROJECT_ROOT)
    return real_stdout


if __name__ == "__main__":
    real_stdout = _prepare()
    from dev_tools.mcp.server import main

    main(stdout=getattr(real_stdout, "buffer", real_stdout))
