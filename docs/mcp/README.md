# OAS MCP 服务

把 OAS 的任务开发能力暴露给支持 MCP 的 AI 客户端（如 opencode），支持：查看项目/配置/任务、
读写文件与检索代码、截图与 OCR、裁剪素材并生成规则、单任务直跑与日志调试。

## 启动方式

opencode 通过项目根 `opencode.json` 自动拉起：

    toolkit\python.exe dev_tools\mcp_server.py

手动调试（在项目根执行）：

    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | toolkit\python.exe dev_tools\mcp_server.py

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
- 规则测试依赖 `dev_tools/assets_test.py`（本地文件，未纳入 git）。
