<div align="center">

# 明珠轩辕AutoScript

<br>

<div>
    <img alt="python" src="https://img.shields.io/badge/python-3.10-%233776AB?logo=python">
</div>
<div>
    <img alt="platform" src="https://img.shields.io/badge/platform-Windows-blueviolet">
</div>
<div>
    <img alt="license" src="https://img.shields.io/badge/license-GPL--3.0-blue">
</div>
<br>

明珠轩辕自动化脚本

一键托管

#### 主仓库: [https://github.com/jiaysr/mzxyAutoScript](https://github.com/jiaysr/mzxyAutoScript)

#### 前端仓库: [https://github.com/jiaysr/mzxyOASX](https://github.com/jiaysr/mzxyOASX)

</div>

明珠轩辕，作为一个手游，每天都有需要重复操作的内容。请减少花费在明珠轩辕上的时间，把一切都交给脚本。

## 致谢与来源 Acknowledgements

本项目**复用自 [OnmyojiAutoScript（OAS）](https://github.com/runhey/OnmyojiAutoScript) 的整套框架**：任务调度系统、设备控制（ADB / uiautomator2 / minitouch / scrcpy / DroidCast / 后台截图…）、截图与找图找色、OCR、配置系统、桌面 GUI / WebUI、MCP 开发工具链等，均来自 OAS 及其上游项目。我们只替换了与游戏相关的内容（任务逻辑、素材、配置项），并在此基础上继续开发。

在此向原作者与社区致以诚挚的感谢：

- 感谢 [runhey](https://github.com/runhey) 与 [OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript) 的所有贡献者——本项目的全部框架基础；
- 感谢 [LmeSzinc](https://github.com/LmeSzinc) 的 [Alas](https://github.com/LmeSzinc/AzurLaneAutoScript)——OAS 的设计思路与架构源自 Alas；
- 感谢 OAS 所使用的开源项目：[RapidOCR](https://github.com/RapidAI/RapidOCR)（本地 OCR 引擎）、[MaaFramework / MaaCommonAssets](https://github.com/MaaXYZ/MaaFramework)（PP-OCRv6 模型与批量识别思路）、[gurs](https://github.com/2833844911/gurs)（滑动轨迹模拟）、[FluentUI](https://github.com/zhuzichu520/FluentUI)（界面控件）等。

本仓库保持与原项目相同的 **GPL-3.0** 许可证，保留原版权声明。感谢原作者的开源精神，让本项目得以存在。

## 功能 Features

- **日常任务**: 每日答题、参与战车
- **战斗任务**: 世界首领、挑战（页面导航）、同服竞技、跨服竞技、上古狩猎、仙府九重天、域外迷窟、斗仙阁
- **脚本设置**: 设备与截图/操作方式、错误处理、调度优化、防封、重启与登录（官方 / 小七渠道）

### 显著特点

- **一条龙托管**: 任务调度大师，按 日常 / 每周 / 限时 自动排期，空闲时段还能自动关游戏、关模拟器省资源
- **通用页面引擎**: 页面寻路、地图探索、虚拟摇杆移动、世界地图传送、简单战斗系统，世界首领 / 域外迷窟等任务共用一套能力
- **聪明答题**: 本地 OCR 识别题干与选项，题库命中直接作答，未知题目交给 AI（DeepSeek）看图作答，支持多设备题库同步
- **多开与排队**: 多实例排队执行，任务开始前自动预热启动模拟器，互不抢设备
- **多种设备方案**: ADB、uiautomator2、minitouch、scrcpy、DroidCast、window_message、MuMu 后台截图（nemu_ipc）
- **本地 OCR**: 内置 PP-OCRv6（RapidOCR 引擎），支持颜色过滤、批量合并识别与结果缓存
- **防封策略**: 内置行为间隔与活跃时段控制，模拟真人节奏

## 安装 Installation

> 安装与使用流程同 [OAS 官方文档](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/installation)，完整版见
> **[docs/installation.md](docs/installation.md)**。

本项目拆成两个仓库：**前端 [mzxyOASX](https://github.com/jiaysr/mzxyOASX)**（Flutter 控制台，启动/停止/看日志）+ **脚本本体**（本仓库）。

1. **下载**：前端从 [mzxyOASX](https://github.com/jiaysr/mzxyOASX) 获取；脚本本体从 [Releases](https://github.com/jiaysr/mzxyAutoScript/releases) 下载（不要下 Source code），或 `git clone -b dev`。脚本目录完全便携，删除即卸载。
2. **配置**：编辑脚本目录下的 `config/deploy.yaml`，重点是

   ```yaml
   Repository: https://github.com/jiaysr/mzxyAutoScript
   Branch: dev
   PypiMirror: https://pypi.tuna.tsinghua.edu.cn/simple
   ```

   OCR 默认已经用 PP-OCRv6，一般不用动。
3. **启动**（三种方式，本质一样）：
   - 从 mzxyOASX 的服务页面选择脚本目录后启动（推荐）；
   - 双击 `deploy/launcher/oas-gui.bat`（发行包里的 `oas.exe`），自动更新 + 装依赖 + 打开 GUI；
   - 命令行：

     ```
     toolkit\python.exe -m deploy.installer    # 拉代码 + 装依赖 + 准备 adb
     toolkit\python.exe server.py              # Web 服务（或 pythonw gui.py 桌面 GUI）
     ```

   首次安装一般 2~5 分钟（取决于网络），看到 `OAS web service startup done` 即成功。
4. **登录**：浏览器打开 `http://127.0.0.1:22267`（端口见启动日志 `[Port]`）；从 mzxyOASX 登录填 `IP:Port`，不要填用户名密码。
5. **使用**：模拟器横屏 1280x720 并打开 ADB → 「脚本设置」里配置设备与截图/操作方式 → 勾选任务并启动。

环境要求：Windows、Python 3.10（发行包已内置 toolkit 环境）、模拟器横屏 1280x720。

## 开发 Development

- 新任务开发指南: [docs/mcp/task_dev_guide.md](docs/mcp/task_dev_guide.md)
- AI 辅助开发（MCP）: [docs/mcp/README.md](docs/mcp/README.md)，让支持 MCP 的 AI 客户端（如 opencode）直接读写任务代码、截图取色、生成素材与规则、单任务直跑与日志调试

## 许可证 LICENSE

This project is licensed under the GNU General Public License v3.0.

本项目基于 OAS 二次开发，同样以 GPL-3.0 开源，保留原项目的版权与许可证声明。

## 声明 Announcement

本软件开源、免费，仅供学习交流使用。开发者团队拥有本项目的最终解释权。使用本软件产生的所有问题与本项目与开发者团队无关。
若你在任何渠道付费购买了本软件，请退款。
请勿在互联网上公开宣传本项目，这既保护项目，也保护每一位使用者。
