# 安装与使用 Installation

> 本项目的安装/使用流程与 [OAS 官方使用文档](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/installation) 完全一致，
> 本文只写**明珠轩辕（mzxy）相关的差异**；遇到这里没写到的细节（模拟器支持、消息推送、常见问题等），
> 直接对照 OAS 文档看即可。

## 架构：前端 + 脚本本体

项目拆成两个仓库，职责不同：

| 仓库 | 作用 |
| --- | --- |
| [mzxyOASX](https://github.com/jiaysr/mzxyOASX) | **前端/控制台**（Flutter，全平台）：启动、停止、看日志、改配置 |
| [mzxyAutoScript](https://github.com/jiaysr/mzxyAutoScript) | **脚本本体**（本项目）：跑任务、连接模拟器、OCR |

- 两者装在**同一台 Windows** 上最省事（推荐）；也可以分机：前端装手机/另一台电脑，登录时填脚本机的 `IP:Port`。
- 第一次使用就不要东一榔头西一棒槌，按本文顺序来。

## 一、下载安装包

### 1. 下载前端 mzxyOASX

- 在 [mzxyOASX 仓库](https://github.com/jiaysr/mzxyOASX) 获取（Windows 桌面版 / 安卓 / iOS / 网页版）。
- 前端只是一个控制台，**关掉它不影响脚本继续跑**。

### 2. 下载脚本本体（本项目）

- 发行包：从 [mzxyAutoScript Releases](https://github.com/jiaysr/mzxyAutoScript/releases) 下载最新的自动安装包
  （**不要下载 Source code 的 zip**）。
- 或者用 git：

  ```
  git clone -b dev https://github.com/jiaysr/mzxyAutoScript
  ```

### 3. 解压与卸载

- 解压到任意目录（建议路径不要有空格），前端和脚本目录分开。
- 脚本目录**完全便携**：完整安装后约 1~2 GB（内置 Python 环境、依赖与 OCR 模型），
  不想用了直接删目录即完成卸载；也可以整目录复制到别的电脑用。
- 如果删除时提示文件被占用，先在 mzxyOASX 里「结束 OAS 服务」再删。

## 二、启动脚本

### 1. 编辑脚本目录下的 `config/deploy.yaml`

这是本项目唯一的使用门槛，按注释改，重点是以下几项：

| 配置项 | 建议值 | 说明 |
| --- | --- | --- |
| `Repository` | `https://github.com/jiaysr/mzxyAutoScript` | 更新源，必须指向本项目 |
| `Branch` | `dev` | 本项目开发主线（`master` 可能落后） |
| `PypiMirror` | `https://pypi.tuna.tsinghua.edu.cn/simple` | 国内装依赖加速 |
| `PythonExecutable` | `./toolkit/python.exe` | 保持默认 |
| `Ocr.OcrModelVersion` | `PP-OCRv6` | 本地 OCR 模型（默认）；想更快可填 `PP-OCRv6-tiny`，回退可选 `PP-OCRv5` |
| `Ocr.OcrCpuThreadNum` | `4` | OCR 的 onnxruntime CPU 线程数 |
| `Webui.WebuiPort` | `22267` | Web 服务端口（被占用就换一个） |

其余保持默认即可。

### 2. 启动（三种方式，本质相同）

1. **从 mzxyOASX 启动（推荐）**：打开前端 → 服务页面 → 选择脚本目录路径 → 点启动。
   前端会执行脚本目录里的安装/更新流程，并把日志显示出来。
2. **双击 `deploy/launcher/oas-gui.bat`**（发行包里的 `oas.exe`）：
   自动拉取代码 → 安装依赖 → 准备 ADB → 打开桌面 GUI。
3. **命令行启动**：

   ```
   toolkit\python.exe -m deploy.installer    # 拉代码 + 装依赖 + 准备 adb
   toolkit\python.exe server.py              # Web 服务（或 pythonw gui.py 桌面 GUI）
   ```

> 首次安装一般 2~5 分钟（取决于网络与机器），耐心等；启动成功的日志长这样：

```
<<< LAUNCHER CONFIG >>>
[Host] 0.0.0.0
[Port] 22267
OAS web service startup done
```

记住 `[Port]` 后面的数字，登录时要用。

### 3. 登录

- 浏览器直接打开 `http://127.0.0.1:22267`。
- 从 mzxyOASX 登录：填 `IP:Port`（同一台机器填 `127.0.0.1:22267`；前端和脚本不同机器就填脚本机的局域网 IP），
  **不要填 Username / Password**。

### 4. 结束服务

- 前端里点「结束 OAS 服务」；
- 或直接关掉 `oas.exe` / 命令行窗口；桌面 GUI 直接退出即可。
- 脚本服务是后台常驻的，平时不建议关（它的任务调度在跑）。

## 三、开始使用

### 1. 配置模拟器

- 分辨率：**横屏 1280x720**。
- 推荐 MuMu（支持后台截图 `nemu_ipc`），雷电、夜神等也能用，参照
  [OAS 模拟器支持](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/emulator-support) 设置。
- 在模拟器设置里**打开 ADB 调试**（有些模拟器默认关闭，会导致安装时 `UIAUTOMATOR2 INIT` 报错）。

### 2. 配置脚本设置

进入「脚本设置」页，第一次必须确认：

- **模拟器 Serial / 设备**：一般 `auto` 就能连上，连不上再手填（如 `127.0.0.1:16384`）。
- **截图方式 / 操作方式**：不确定就都留 `auto`；MuMu 后台截图、DroidCast、scrcpy 等可选项按机器情况调优。
- **错误处理 / 调度 / 防封**：按自己的作息配置（活跃时段、任务间隔等）。

### 3. 配置任务并启动

- 在任务列表里勾选需要的任务（每日答题、参与战车、世界首领、上古狩猎、仙府九重天、域外迷窟、斗仙阁、银贝礼盒…），
  设置「启用」与「下一次运行时间」，任务专属参数按页面说明填。
- 点开关启动，然后就可以挂机了；配置不对会以日志形式提示。

## 四、常见问题

- **更新后 OCR/依赖报错**：重跑一次 `toolkit\python.exe -m deploy.installer`，
  它会按 `requirements.txt` 补齐依赖（本项目用 RapidOCR + PP-OCRv6，模型随仓库自带）。
- **启动日志出现 `UIAUTOMATOR2 INIT ... exitCode 1`**：通常是还有别的模拟器实例开着但没开 ADB，
  关掉它或在它的设置里打开 ADB 后重跑；不用 uiautomator2 的话，把 `deploy.yaml` 里
  `InstallUiautomator2` 改成 `false` 也能绕开。
- **端口被占用**：改 `deploy.yaml` 的 `WebuiPort`，登录端口同步改。
- **日志与错误现场**：脚本目录 `log/` 下（报错截图在 `log/error/`）。
- **文件被占用删不掉**：先在 mzxyOASX 里结束 OAS 服务再删。
- 其它问题（截图黑屏、任务卡住、消息推送配置…）优先对照
  [OAS 常见问题](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/frequently-asked-questions)。
