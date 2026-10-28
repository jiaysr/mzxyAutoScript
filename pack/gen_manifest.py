# -*- coding: utf-8 -*-
"""
生成 manifest.json（引擎更新清单）

客户端只读这一个文件，决定要不要更新 code / env：

    channel 变了      → 全量替换 code
    code.version 变了 → 下新的 code 包
    env.id 变了       → 下新的 env 包（几百 MB，只有 requirements 变了才会变）

用法：
    python pack/gen_manifest.py --code dist/code-abc1234.zip \
        --env dist/env-py310-a1b2c3d4.zip --channel beta --out dist/manifest.json

    # 资源放在别的域名（OSS/CDN）时用 --base-url 前缀
    python pack/gen_manifest.py ... --base-url https://cdn.example.com/engine
"""
import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

# CI / 非 UTF-8 控制台下中文输出会抛 UnicodeEncodeError，统一改成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json_in_zip(path: Path, member: str) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            if member in zf.namelist():
                return json.loads(zf.read(member).decode('utf-8'))
    except Exception as e:
        print(f'[warn] 读取 {path.name}!{member} 失败：{e}')
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description='生成引擎更新清单')
    parser.add_argument('--code', required=True, help='code-<ver>.zip 路径')
    parser.add_argument('--env', default='', help='env-<env_id>.zip 路径（可复用已有版本时省略）')
    parser.add_argument('--env-id', default='', help='复用已有 env 时直接给 id，配合 --env-url')
    parser.add_argument('--env-url', default='', help='复用已有 env 时直接给 url')
    parser.add_argument('--env-sha256', default='', help='复用已有 env 时直接给 sha256')
    parser.add_argument('--env-size', type=int, default=0, help='复用已有 env 时直接给大小')
    parser.add_argument('--channel', default='beta', choices=['stable', 'beta'])
    parser.add_argument('--api-version', type=int, default=1)
    parser.add_argument('--base-url', default='', help='资源前缀，默认用相对文件名')
    parser.add_argument('--notes', default='', help='本次更新说明（多行）')
    parser.add_argument('--notes-file', default='', help='从文件读更新说明，CI 里用它避免转义问题')
    parser.add_argument('--out', default='dist/manifest.json')
    args = parser.parse_args()

    code_path = Path(args.code)
    if not code_path.exists():
        raise SystemExit(f'找不到 {code_path}')

    code_meta = read_json_in_zip(code_path, 'version.json')
    code_version = code_meta.get('version') or code_path.stem.replace('code-', '')

    manifest = {
        'channel': args.channel,
        'api_version': args.api_version,
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'code': {
            'version': code_version,
            'url': f'{args.base_url}/{code_path.name}' if args.base_url else code_path.name,
            'sha256': sha256_file(code_path),
            'size': code_path.stat().st_size,
        },
    }

    # 更新说明：客户端更新完会展示这段文字
    notes = args.notes
    if args.notes_file:
        notes_path = Path(args.notes_file)
        if notes_path.exists():
            notes = notes_path.read_text(encoding='utf-8')
        else:
            print(f'[warn] 找不到 {notes_path}，更新说明留空')
    if notes.strip():
        manifest['notes'] = notes.strip()

    if args.env:
        env_path = Path(args.env)
        if not env_path.exists():
            raise SystemExit(f'找不到 {env_path}')
        env_meta = read_json_in_zip(env_path, 'env.json')
        env_id = env_meta.get('env_id') or env_path.stem.replace('env-', '')
        manifest['env'] = {
            'id': env_id,
            'url': f'{args.base_url}/{env_path.name}' if args.base_url else env_path.name,
            'sha256': sha256_file(env_path),
            'size': env_path.stat().st_size,
            # 记下打这个 env 包时的 requirements 指纹，CI 用它判断要不要重打
            'requirements_sha256': env_meta.get('requirements_sha256', ''),
        }
    elif args.env_id and args.env_url:
        # 复用历史 Release 里已有的 env 包，不重传几百 MB
        manifest['env'] = {
            'id': args.env_id,
            'url': args.env_url,
            'sha256': args.env_sha256,
            'size': args.env_size,
        }
    else:
        raise SystemExit('要么给 --env，要么同时给 --env-id 和 --env-url')

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f'\n[manifest] 已写入 {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
