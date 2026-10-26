# This Python file uses the following encoding: utf-8
"""
引擎目录布局

两个目录必须分开：

- **代码目录（ENGINE_DIR / cwd）**：module / tasks / assets / bin，随版本整体替换
- **数据目录（DATA_DIR）**：config / log，跨版本保留，更新时永不覆盖

默认 DATA_DIR = 当前工作目录，保持旧用法（配置就在脚本目录下的 config/）。
Electron 启动器会传 ``MZXY_DATA_DIR``，把用户数据放到引擎目录之外，
这样切换 code 版本时配置和日志不会丢。

    MZXY_DATA_DIR=%LOCALAPPDATA%\\mzxy\\data
    → 配置在 data/config，日志在 data/log
"""
import os
import shutil
from pathlib import Path

# 仓库根目录（= 代码目录，也是运行时 cwd）
ENGINE_DIR = Path(__file__).resolve().parent.parent


def _resolve_data_dir() -> Path:
    env = os.environ.get('MZXY_DATA_DIR', '').strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd()


DATA_DIR = _resolve_data_dir()
CONFIG_DIR = DATA_DIR / 'config'
LOG_DIR = DATA_DIR / 'log'

# 模板随代码发布，数据目录里没有时种一份过去
TEMPLATE_FILE = ENGINE_DIR / 'config' / 'template.json'


def ensure_dirs() -> None:
    """建好数据目录，并在首次启动时种下 template.json"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    template = CONFIG_DIR / 'template.json'
    if not template.exists() and TEMPLATE_FILE.exists():
        shutil.copy2(TEMPLATE_FILE, template)
