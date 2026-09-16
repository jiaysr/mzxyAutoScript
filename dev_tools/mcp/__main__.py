# This Python file uses the following encoding: utf-8
"""python -m dev_tools.mcp 入口（cwd 需为项目根）。"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

real_stdout = sys.stdout
sys.stdout = sys.stderr if sys.stderr is not None else open(os.devnull, "w", encoding="utf-8")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dev_tools.mcp.server import main  # noqa: E402

main(stdout=getattr(real_stdout, "buffer", real_stdout))
