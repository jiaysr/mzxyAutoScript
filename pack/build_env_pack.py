# -*- coding: utf-8 -*-
"""
打 env 包（Python 运行时 + 依赖 + OCR 模型）

产物：dist/env-<env_id>.zip，解压后是：

    toolkit/            python.exe + Lib/site-packages（预装全部依赖，无 pip、无 git）
    bin/ocr_model/      PP-OCR 模型（66MB，跟代码版本无关）
    env.json            {env_id, python, platform, requirements_sha256, built_at}

env_id 的算法（发布端和客户端必须一致）：

    sha256(requirements.txt 内容 + python 版本 + 平台 + 模型文件清单)[:8]
    前缀固定 py310-

也就是说：只有 requirements 变了、或换了 Python/模型，env_id 才会变，
其余版本一律复用同一个 env 包，不用重下几百 MB。

用法：
    python pack/build_env_pack.py --out dist
    python pack/build_env_pack.py --out dist --source ./toolkit --compress-level 1
"""
import argparse
import hashlib
import json
import shutil
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
PLATFORM_TAG = 'win-x64'
PYTHON_TAG = 'py310'

# 打包配方版本：改了剔除清单 / 目录结构就 +1，保证 env_id 跟着变
# （否则内容变了但 id 没变，客户端会误以为不用更新）
PACK_RECIPE_VERSION = 3

# 这些依赖代码里 0 处 import，纯粹是 OAS 历史遗留，打包时丢掉
# toolkit 顶层
EXCLUDE_TOP = {'Git'}          # 43MB，运行时用不到

# toolkit/Lib/site-packages 下的整包剔除（代码里 0 处 import）
EXCLUDE_SITE = {
    'frida', 'frida_tools',    # 127MB
    'oashya',                  # 13MB
    'oas_checkin_biggod',      # 35MB
    'ppocronnx',               # 12MB
    'pip', 'pip-*',            # 14MB
    # 注意：pythonwin 不能删，win32ui.pyd 在里面（module/device 的窗口截图要用）
}
EXCLUDE_DIR_NAMES = {'__pycache__', '.git'}
EXCLUDE_SUFFIXES = {'.pyc', '.pyo'}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def python_version(python: Path) -> str:
    try:
        out = subprocess.run([str(python), '-c',
                              'import sys;print("%d.%d.%d" % sys.version_info[:3])'],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return '3.10.0'


def model_manifest(bin_dir: Path) -> list:
    """模型文件清单：只取名字和大小，避免读 66MB 内容"""
    items = []
    if not bin_dir.exists():
        return items
    for path in sorted(bin_dir.rglob('*')):
        if path.is_file():
            items.append([path.relative_to(bin_dir).as_posix(), path.stat().st_size])
    return items


def compute_env_id(requirements: Path, py_ver: str, models: list) -> str:
    h = hashlib.sha256()
    h.update(requirements.read_bytes() if requirements.exists() else b'')
    h.update(py_ver.encode())
    h.update(PLATFORM_TAG.encode())
    h.update(str(PACK_RECIPE_VERSION).encode())
    for name, size in models:
        h.update(name.encode())
        h.update(str(size).encode())
    return f'{PYTHON_TAG}-{h.hexdigest()[:8]}'


def _make_ignore(source: Path):
    """copytree 的 ignore：按目录层级决定剔哪些"""
    def _ignore(dir_path, names):
        p = Path(dir_path)
        try:
            rel = p.relative_to(source).parts
        except ValueError:
            rel = ()
        in_site = len(rel) >= 2 and rel[-2:] == ('Lib', 'site-packages')
        ignored = set()
        for name in names:
            if name in EXCLUDE_DIR_NAMES:
                ignored.add(name)
                continue
            if Path(name).suffix.lower() in EXCLUDE_SUFFIXES:
                ignored.add(name)
                continue
            if in_site and _skip_site(name):
                ignored.add(name)
        return ignored
    return _ignore


def _skip_site(name: str) -> bool:
    for pattern in EXCLUDE_SITE:
        if pattern.endswith('*'):
            if name.startswith(pattern[:-1]):
                return True
        elif name == pattern:
            return True
    return False


def stage(source: Path, staging: Path) -> int:
    """把 toolkit 复制到 staging/toolkit，顺便剔除无用依赖，返回复制字节数"""
    target = staging / 'toolkit'
    target.mkdir(parents=True, exist_ok=True)

    for entry in source.iterdir():
        if entry.name in EXCLUDE_TOP:
            continue
        dest = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, ignore=_make_ignore(source), dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)

    return sum(p.stat().st_size for p in target.rglob('*') if p.is_file())


def build(out_dir: Path, source: Path, compress_level: int) -> Path:
    requirements = ROOT / 'requirements.txt'
    python = source / 'python.exe'
    if not python.exists():
        raise SystemExit(f'找不到 {python}，请用 --source 指定 toolkit 目录')

    py_ver = python_version(python)
    models = model_manifest(ROOT / 'bin' / 'ocr_model')
    env_id = compute_env_id(requirements, py_ver, models)

    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / f'.stage-{env_id}'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    print(f'[env] 来源      {source}')
    print(f'[env] Python    {py_ver}')
    print(f'[env] env_id    {env_id}')
    print('[env] 正在复制运行时…')
    copied = stage(source, staging)

    # OCR 模型
    ocr_src = ROOT / 'bin' / 'ocr_model'
    if ocr_src.exists():
        shutil.copytree(ocr_src, staging / 'bin' / 'ocr_model', dirs_exist_ok=True)

    meta = {
        'env_id': env_id,
        'python': py_ver,
        'platform': PLATFORM_TAG,
        'requirements_sha256': sha256_file(requirements) if requirements.exists() else '',
        'models': len(models),
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    }
    (staging / 'env.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    zip_path = out_dir / f'env-{env_id}.zip'
    print(f'[env] 正在压缩（level={compress_level}），原始 {copied / 1024 / 1024:.0f} MB…')
    files = [p for p in sorted(staging.rglob('*')) if p.is_file()]
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=compress_level) as zf:
        for path in files:
            zf.write(path, path.relative_to(staging).as_posix())

    shutil.rmtree(staging, ignore_errors=True)

    digest = sha256_file(zip_path)
    size = zip_path.stat().st_size
    print(f'[env] 文件数    {len(files)}')
    print(f'[env] 产物      {zip_path}（{size / 1024 / 1024:.1f} MB）')
    print(f'[env] sha256    {digest}')
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description='打 env 包')
    parser.add_argument('--out', default='dist', help='输出目录，默认 dist/')
    parser.add_argument('--source', default='toolkit', help='运行时来源目录，默认 ./toolkit')
    parser.add_argument('--compress-level', type=int, default=6,
                        help='zlib 压缩级别 0-9，本地验证用 1 更快')
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = ROOT / source
    build(ROOT / args.out, source, args.compress_level)
    return 0


if __name__ == '__main__':
    sys.exit(main())
