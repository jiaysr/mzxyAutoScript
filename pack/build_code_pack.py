# -*- coding: utf-8 -*-
"""
打 code 包（引擎的代码层）

产物：dist/code-<version>.zip

包含：
    server.py  script.py                 引擎入口
    module/  tasks/  assets/  deploy/  dev_tools/
    config/template.json
    bin/{droidcast,hermit,scrcpy}     小体积运行时资源（2.9MB）

不含：
    bin/ocr_model                      66MB，跟代码版本无关，放 env 包
    config/*.json 用户配置              属于数据目录，永不随包覆盖
    tests/ docs/ pack/ toolkit/ fluentui/ 开发用

用法：
    python pack/build_code_pack.py --out dist
    python pack/build_code_pack.py --version 0.4.3 --out dist
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# CI / 非 UTF-8 控制台下中文输出会抛 UnicodeEncodeError，统一改成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent

# 整目录收进包
INCLUDE_DIRS = ('module', 'tasks', 'assets', 'deploy', 'dev_tools')
# 根目录文件（引擎入口就在根上，漏了会起不来）
INCLUDE_FILES = ('server.py', 'script.py', 'config/template.json')
# bin 下只带小的，ocr_model 留给 env
INCLUDE_BIN = ('droidcast', 'hermit', 'scrcpy')

EXCLUDE_DIR_NAMES = {
    '__pycache__', '.git', '.idea', '.vscode', '.pytest_cache',
    'launcher', 'docker',   # deploy 下的启动器/容器配置，引擎用不到
}
EXCLUDE_SUFFIXES = {'.pyc', '.pyo', '.log', '.tmp'}

# 引擎与 UI 的接口版本，破坏性改动时才 +1
API_VERSION = 1


def git(*args: str) -> str:
    try:
        out = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True,
                             encoding='utf-8', errors='replace')
        return out.stdout.strip() if out.returncode == 0 else ''
    except Exception:
        return ''


def resolve_version(explicit: str = '') -> str:
    if explicit:
        return explicit
    # dev 通道用 commit sha 当版本号，不用维护语义版本
    sha = git('rev-parse', '--short', 'HEAD') or time.strftime('%Y%m%d-%H%M%S')
    dirty = git('status', '--porcelain')
    return f'{sha}-dirty' if dirty else sha


def iter_files():
    """按稳定顺序产出 (绝对路径, zip 内相对路径)"""
    items = []

    for dirname in INCLUDE_DIRS:
        base = ROOT / dirname
        if not base.exists():
            print(f'[warn] 目录不存在，跳过：{dirname}')
            continue
        for path in base.rglob('*'):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            items.append((path, path.relative_to(ROOT).as_posix()))

    for rel in INCLUDE_FILES:
        path = ROOT / rel
        if path.exists():
            items.append((path, rel))

    bin_dir = ROOT / 'bin'
    for name in INCLUDE_BIN:
        sub = bin_dir / name
        if not sub.exists():
            continue
        if sub.is_file():
            items.append((sub, f'bin/{name}'))
            continue
        for path in sub.rglob('*'):
            if path.is_file() and path.suffix.lower() not in EXCLUDE_SUFFIXES:
                items.append((path, path.relative_to(ROOT).as_posix()))

    items.sort(key=lambda x: x[1])
    return items


def build(out_dir: Path, version: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f'code-{version}.zip'

    meta = {
        'version': version,
        'api_version': API_VERSION,
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'commit': git('rev-parse', 'HEAD'),
        'channel': git('rev-parse', '--abbrev-ref', 'HEAD'),
    }

    items = iter_files()
    total = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # version.json 放最前，安装时可以不解压就读取
        zf.writestr('version.json', json.dumps(meta, ensure_ascii=False, indent=2))
        for path, arcname in items:
            zf.write(path, arcname)
            total += path.stat().st_size

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size = zip_path.stat().st_size
    print(f'[code] 版本      {version}')
    print(f'[code] 文件数    {len(items)}（原始 {total / 1024 / 1024:.1f} MB）')
    print(f'[code] 产物      {zip_path}（{size / 1024 / 1024:.1f} MB）')
    print(f'[code] sha256    {digest}')
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description='打 code 包')
    parser.add_argument('--out', default='dist', help='输出目录，默认 dist/')
    parser.add_argument('--version', default='', help='版本号，默认取 git describe')
    args = parser.parse_args()

    version = resolve_version(args.version)
    build(ROOT / args.out, version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
