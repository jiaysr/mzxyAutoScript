# OAS MCP 服务

把 OAS 的任务开发能力暴露给支持 MCP 的 AI 客户端（如 opencode），支持：查看项目/配置/任务、
读写文件与检索代码、截图与 OCR、裁剪素材并生成规则、单任务直跑与日志调试。

## 启动方式

opencode 打开本项目时，会按项目根 `opencode.json` 自动拉起（相对路径，换电脑无需改配置）：

    cmd /c dev_tools\mcp_launch.bat
    # 等价于 dev_tools\mcp_launch.bat 内部执行：..\toolkit\python.exe mcp_server.py

手动调试（在项目根执行）：

    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | cmd /c dev_tools\mcp_launch.bat

## 新电脑适配（拉取代码后）

1. **准备 toolkit 环境**（`toolkit/` 不进 git）：运行 `deploy\launcher\oas-gui.bat` 完成一键安装，
   或手动执行：

       toolkit\python.exe -m pip install -r requirements.txt

   MCP 不引入新的第三方依赖，用项目 `requirements.txt` 即可（含 cv2/numpy/psutil/filelock 等）。
2. **用 opencode 打开项目**：`opencode.json` 里的命令是工作区相对路径，opencode 会以工作区为
   基准解析；`dev_tools\mcp_launch.bat` 自己按脚本位置定位项目根，所以**不需要修改任何配置**。
3. **验证**：对话里让 AI 调 `get_project_info`；或命令行执行 `dev_tools\mcp_launch.bat` 看是否报
   `toolkit\python.exe not found`（报错说明第 1 步没做完）。
4. **设备配置**：`config/*.json` 不进 git，首次使用会基于 `config/template.json` 自动生成 `oas1`；
   需要在 GUI（或直接改 `config/oas1.json` 的 `script.device.serial/handle`）里配置模拟器。

### 兜底：opencode 未加载该 MCP

若工具列表里没有 `oas_*`（例如 opencode 从其它目录启动导致相对路径解析失败），把
`opencode.json` 的 `command` 改为绝对路径即可：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "oas": {
      "type": "local",
      "command": ["cmd", "/c", "<项目绝对路径>\\dev_tools\\mcp_launch.bat"],
      "cwd": "<项目绝对路径>",
      "timeout": 30000,
      "enabled": true
    }
  }
}
```

非 Windows 环境（本项目其余部分也是 Windows 专用）可直接用：

```json
"command": ["toolkit/python.exe", "dev_tools/mcp_server.py"],
"cwd": "<项目绝对路径>",
```

## 工具清单（23 个）

| 分组 | 工具 |
| --- | --- |
| 项目/配置 | `get_project_info`、`list_configs`、`select_config`、`list_tasks` |
| 文件/知识 | `list_files`、`read_file`、`write_file`、`delete_file`、`search_code`、`list_docs`、`read_doc` |
| 素材/规则 | `screenshot`、`ocr_region`、`crop_asset`、`add_rule`、`read_rule_file`、`test_rule` |
| 运行调试 | `run_task`、`list_runs`、`get_run_logs`、`stop_run`、`get_error_screenshots`、`run_and_get_result` |

## 典型流程

1. `get_project_info` → `select_config` 选定配置
2. `screenshot` 看当前画面（需要时手动把游戏切到目标页面后再截图）
3. `crop_asset` 裁素材 → `add_rule` 写规则并自动重生 `assets.py` → `test_rule` 验证命中
4. `read_doc task_dev_guide` + `search_code` 参考相似任务 → `write_file` 写 `script_task.py` / `config.py`
5. `run_task` → `get_run_logs` 调试；失败时 `get_error_screenshots` 看错误现场

## 运行时目录

- 截图：`log/mcp/screenshots/`
- 运行日志与临时 runner：`log/mcp/runs/`（可安全清除）
- 设备单例在 MCP 进程内懒加载；同一配置不要与 GUI 脚本同时运行

## 注意事项

- 依赖项目锁定的 toolkit 解释器（`toolkit/python.exe`）与已安装依赖；不引入新的第三方包。
- 规则校验/写入与 annotator 网页共用同一套实现（`module/server/tool.py`），写出的 JSON 与网页完全一致。
- 规则测试使用 `dev_tools/assets_test.py`（已纳入 git，随代码拉取）。
- 启动耗时约 0.3s（工具模块按需加载）；`opencode.json` 里 `timeout` 设为 30s 是为慢机器留余量。
