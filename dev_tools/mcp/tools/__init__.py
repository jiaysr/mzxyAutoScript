# This Python file uses the following encoding: utf-8
"""MCP 工具集合。"""
from dev_tools.mcp.tools import asset, docs, files, project, runner


def register_all(registry) -> None:
    files.register(registry)
    project.register(registry)
    docs.register(registry)
    asset.register(registry)
    runner.register(registry)
