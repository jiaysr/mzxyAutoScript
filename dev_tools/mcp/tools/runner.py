# This Python file uses the following encoding: utf-8
"""单任务直跑：生成临时 runner、收集日志、停止进程、错误截图。"""
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import (
    ERROR_LOG_ROOT,
    LOG_ROOT,
    PROJECT_ROOT,
    PYTHON_EXECUTABLE,
    RUN_LOG_DIR,
    TASKS_ROOT,
    runtime,
)
from dev_tools.mcp.server import (
    ToolRegistry,
    image_content,
    optional_bool,
    require_int,
    require_str,
    schema_object,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}

RUNNER_TEMPLATE = '''# -*- coding: utf-8 -*-
# 由 OAS MCP 自动生成的单任务 runner，可安全删除
import sys
sys.path.insert(0, r"{project_root}")
from module.config.config import Config
from module.device.device import Device
from module.exception import TaskEnd
from tasks.{task}.script_task import ScriptTask

config = Config("{config}")
device = Device(config)
try:
    ScriptTask(config, device).run()
except TaskEnd as e:
    print(f"TaskEnd: {{e}}")
'''


@dataclass
class RunRecord:
    run_id: str
    task: str
    config: str
    log_file: Path
    started_at: float
    timeout_seconds: int
    process: object = field(repr=False, default=None)
    status: str = "running"
    exit_code: Optional[int] = None
    warning: str = ""


class RunManager:
    def __init__(self) -> None:
        self._runs: dict = {}

    # ---------------------------- 启动 ----------------------------
    def start_command(
        self,
        command: list,
        *,
        run_id: str,
        task: str = "",
        config: str = "",
        timeout_seconds: int = 0,
        cwd: Optional[Path] = None,
    ) -> RunRecord:
        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = RUN_LOG_DIR / f"{run_id}.log"
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        handle = open(log_file, "ab", buffering=0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd or PROJECT_ROOT),
                stdout=handle,
                stderr=subprocess.STDOUT,
                env=env,
            )
        finally:
            handle.close()
        record = RunRecord(
            run_id=run_id,
            task=task,
            config=config,
            log_file=log_file,
            started_at=time.time(),
            timeout_seconds=timeout_seconds,
            process=process,
        )
        self._runs[run_id] = record
        return record

    def _conflict_warning(self, config: str) -> str:
        for record in self._runs.values():
            self.refresh(record)
            if record.status == "running" and record.config == config:
                return f"MCP 已有 {config} 的运行中任务: {record.run_id}"
        log_file = LOG_ROOT / f"{time.strftime('%Y-%m-%d')}_{config}.txt"
        if log_file.exists():
            age = time.time() - log_file.stat().st_mtime
            if age < 10:
                return f"检测到 {log_file.name} 刚刚有写入（{age:.0f}s 前），可能有脚本正在运行"
        return ""

    def start(
        self,
        task: str,
        config: str = "",
        timeout_seconds: int = 0,
        force: bool = False,
    ) -> RunRecord:
        task = task.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", task):
            raise McpToolError(f"非法任务名: {task!r}")
        task_dir = TASKS_ROOT / task
        if not (task_dir / "script_task.py").is_file():
            raise McpToolError(
                f"任务不存在或缺少 script_task.py: tasks/{task}",
                hint="用 list_tasks 查看可用任务",
            )
        config = runtime.resolve_config(config)
        warning = self._conflict_warning(config)
        if warning and not force:
            raise McpToolError(warning, hint="确认可以运行请加 force=true")
        run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        runner_file = RUN_LOG_DIR / f"{run_id}_{task}.py"
        runner_file.write_text(
            RUNNER_TEMPLATE.format(project_root=PROJECT_ROOT.as_posix(), task=task, config=config),
            encoding="utf-8",
        )
        record = self.start_command(
            [str(PYTHON_EXECUTABLE), str(runner_file)],
            run_id=run_id,
            task=task,
            config=config,
            timeout_seconds=timeout_seconds,
        )
        record.warning = warning
        return record

    # ---------------------------- 状态 ----------------------------
    def refresh(self, record: RunRecord) -> RunRecord:
        process = record.process
        if process is None or record.status != "running":
            return record
        exit_code = process.poll()
        if exit_code is not None:
            record.exit_code = exit_code
            record.status = "exited" if exit_code == 0 else "error"
            return record
        if record.timeout_seconds and time.time() - record.started_at > record.timeout_seconds:
            self._terminate(record)
            record.status = "timeout"
        return record

    @staticmethod
    def _terminate(record: RunRecord) -> None:
        process = record.process
        if process is None or process.poll() is not None:
            return
        try:
            import psutil

            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            parent.terminate()
            _, alive = psutil.wait_procs([parent, *children], timeout=5)
            for item in alive:
                item.kill()
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            record.exit_code = process.poll()
        except Exception:  # noqa: BLE001
            pass

    def get(self, run_id: str = "") -> RunRecord:
        if not self._runs:
            raise McpToolError("还没有任何运行记录")
        if not run_id:
            return self.refresh(max(self._runs.values(), key=lambda item: item.started_at))
        record = self._runs.get(run_id)
        if record is None:
            raise McpToolError(f"运行记录不存在: {run_id}", hint="用 list_runs 查看")
        return self.refresh(record)

    def stop(self, run_id: str = "") -> str:
        record = self.get(run_id)
        if record.process is not None and record.process.poll() is None:
            self._terminate(record)
            record.status = "stopped"
        return (
            f"已停止 {record.run_id}（task={record.task}, config={record.config}, "
            f"status={record.status}）"
        )

    def stop_all(self) -> None:
        for record in list(self._runs.values()):
            try:
                if record.process is not None and record.process.poll() is None:
                    self.stop(record.run_id)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------- 日志 ----------------------------
    def read_log(
        self, run_id: str = "", lines: int = 200, keyword: str = "", offset: int = 0
    ) -> tuple:
        record = self.get(run_id)
        if not record.log_file.exists():
            return "", 0
        raw = record.log_file.read_text(encoding="utf-8", errors="replace")
        clean = ANSI_RE.sub("", raw)
        if offset > 0:
            return clean[offset:], len(clean)
        if keyword:
            matched = [line for line in clean.splitlines() if keyword.lower() in line.lower()]
            return "\n".join(matched[-lines:]), len(clean)
        return "\n".join(clean.splitlines()[-lines:]), len(clean)

    def describe_runs(self) -> str:
        if not self._runs:
            return "还没有任何运行记录"
        lines = []
        for record in sorted(self._runs.values(), key=lambda item: item.started_at, reverse=True):
            self.refresh(record)
            lines.append(
                f"{record.run_id}: task={record.task} config={record.config} "
                f"status={record.status} exit_code={record.exit_code} "
                f"log={record.log_file.relative_to(PROJECT_ROOT).as_posix()}"
            )
        return "\n".join(lines)


manager = RunManager()


# ---------------------------- 工具函数 ----------------------------
def _run_summary(record: RunRecord) -> str:
    return (
        f"run_id={record.run_id} task={record.task} config={record.config} "
        f"status={record.status} exit_code={record.exit_code}\n"
        f"日志文件: {record.log_file.relative_to(PROJECT_ROOT).as_posix()}"
    )


def run_task(args: dict) -> str:
    task = require_str(args, "task")
    config = require_str(args, "config", required=False, default="")
    timeout_seconds = require_int(args, "timeout_seconds", default=0, minimum=0)
    force = optional_bool(args, "force", False)
    record = manager.start(task, config=config, timeout_seconds=timeout_seconds, force=force)
    lines = [_run_summary(record)]
    if record.warning:
        lines.append(f"警告: {record.warning}")
    lines.append("用 get_run_logs 查看输出，stop_run 停止。")
    return "\n".join(lines)


def list_runs(args: dict) -> str:
    return manager.describe_runs()


def get_run_logs(args: dict) -> str:
    run_id = require_str(args, "run_id", required=False, default="")
    lines = require_int(args, "lines", default=200, minimum=1, maximum=5000)
    keyword = require_str(args, "keyword", required=False, default="")
    offset = require_int(args, "offset", default=0, minimum=0)
    content, next_offset = manager.read_log(run_id, lines=lines, keyword=keyword, offset=offset)
    record = manager.get(run_id)
    header = (
        f"[{record.run_id} status={record.status} exit_code={record.exit_code} "
        f"next_offset={next_offset}]"
    )
    body = content.strip() or "(暂无日志输出)"
    return f"{header}\n{body}"


def stop_run(args: dict) -> str:
    run_id = require_str(args, "run_id", required=False, default="")
    return manager.stop(run_id)


def get_error_screenshots(args: dict) -> list:
    limit = require_int(args, "limit", default=5, minimum=1, maximum=50)
    dir_name = require_str(args, "dir", required=False, default="")
    file_name = require_str(args, "file", required=False, default="")
    if not ERROR_LOG_ROOT.is_dir():
        return [{"type": "text", "text": "还没有错误截图（log/error 不存在）"}]

    if dir_name and file_name:
        target_dir = (ERROR_LOG_ROOT / dir_name).resolve()
        try:
            target_dir.relative_to(ERROR_LOG_ROOT.resolve())
        except ValueError as exc:
            raise McpToolError(f"路径越界: {dir_name}") from exc
        target = (target_dir / file_name).resolve()
        try:
            target.relative_to(target_dir)
        except ValueError as exc:
            raise McpToolError(f"路径越界: {file_name}") from exc
        if not target.is_file():
            raise McpToolError(f"文件不存在: {dir_name}/{file_name}")
        if target.suffix.lower() in IMAGE_SUFFIXES:
            return [{"type": "text", "text": f"{dir_name}/{file_name}"}, image_content(target)]
        text = target.read_text(encoding="utf-8", errors="replace")[-200000:]
        return [{"type": "text", "text": f"{dir_name}/{file_name}\n{text}"}]

    dirs = sorted(
        (path for path in ERROR_LOG_ROOT.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )[:limit]
    if not dirs:
        return [{"type": "text", "text": "log/error 下没有错误目录"}]
    lines: list[str] = []
    for folder in dirs:
        files = sorted(path.name for path in folder.iterdir() if path.is_file())
        images = [name for name in files if Path(name).suffix.lower() in IMAGE_SUFFIXES]
        lines.append(f"{folder.name}/: {len(images)} 张截图, log.txt={'log.txt' in files}")
    lines.append("用 get_error_screenshots(dir=<目录名>, file=<文件名>) 读取具体文件。")
    return [{"type": "text", "text": "\n".join(lines)}]


def run_and_get_result(args: dict) -> str:
    task = require_str(args, "task")
    config = require_str(args, "config", required=False, default="")
    wait_seconds = require_int(args, "wait_seconds", default=60, minimum=1, maximum=900)
    lines = require_int(args, "lines", default=200, minimum=1, maximum=5000)
    timeout_seconds = require_int(args, "timeout_seconds", default=0, minimum=0)
    force = optional_bool(args, "force", True)
    record = manager.start(task, config=config, timeout_seconds=timeout_seconds, force=force)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        manager.refresh(record)
        if record.status != "running":
            break
        time.sleep(1)
    manager.refresh(record)
    content, _ = manager.read_log(record.run_id, lines=lines)
    body = ANSI_RE.sub("", content).strip() or "(暂无日志输出)"
    text = f"{_run_summary(record)}\n--- 日志尾部 ---\n{body}"
    if record.status in ("error", "timeout") or (record.exit_code not in (None, 0)):
        text += "\n\n运行未成功，可用 get_error_screenshots 查看错误现场。"
    return text


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "run_task",
        "单任务直跑（tasks/<task>/script_task.py），日志落 log/mcp/runs/，返回 run_id。",
        schema_object(
            {
                "task": {"type": "string"},
                "config": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 0},
                "force": {"type": "boolean", "default": False},
            },
            ["task"],
        ),
    )(run_task)
    registry.tool("list_runs", "列出本会话的运行记录与状态。", schema_object())(list_runs)
    registry.tool(
        "get_run_logs",
        "读取运行日志尾部，支持 keyword 过滤与 offset 增量读取。",
        schema_object(
            {
                "run_id": {"type": "string"},
                "lines": {"type": "integer", "default": 200},
                "keyword": {"type": "string"},
                "offset": {"type": "integer", "default": 0},
            }
        ),
    )(get_run_logs)
    registry.tool(
        "stop_run",
        "停止运行中的任务进程树（run_id 缺省取最近一次）。",
        schema_object({"run_id": {"type": "string"}}),
    )(stop_run)
    registry.tool(
        "get_error_screenshots",
        "列出 log/error 下的错误现场；指定 dir+file 时返回截图或 log.txt 内容。",
        schema_object(
            {
                "limit": {"type": "integer", "default": 5},
                "dir": {"type": "string"},
                "file": {"type": "string"},
            }
        ),
    )(get_error_screenshots)
    registry.tool(
        "run_and_get_result",
        "组合工具：启动任务 → 等待 → 返回状态与日志尾部（失败时提示错误截图）。",
        schema_object(
            {
                "task": {"type": "string"},
                "config": {"type": "string"},
                "wait_seconds": {"type": "integer", "default": 60},
                "lines": {"type": "integer", "default": 200},
                "timeout_seconds": {"type": "integer", "default": 0},
                "force": {"type": "boolean", "default": True},
            },
            ["task"],
        ),
    )(run_and_get_result)
