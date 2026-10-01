# This Python file uses the following encoding: utf-8
"""
题库网页工具

用法：
    python tasks/Quiz/bank/bank_tool.py            # 把题库嵌入 index.html 的离线快照
    python tasks/Quiz/bank/bank_tool.py --open     # 嵌入后顺便用浏览器打开 index.html

index.html 是极简风格的题库编辑器：查看/筛选/新增/改答案，
改动保存在浏览器 localStorage，点「导出 quiz_zh.json」下载后覆盖主库文件即可。
"""
import argparse
import io
import json
import os
import sys
import webbrowser
from pathlib import Path

BANK_DIR = Path(__file__).resolve().parent
INDEX_FILE = BANK_DIR / 'index.html'
BANK_FILE = BANK_DIR / 'quiz_zh.json'
SNAPSHOT_ID = 'bank-snapshot'
EMPTY_SNAPSHOT = '{"version": 1, "updated": "", "questions": []}'


def load_bank() -> dict:
    if BANK_FILE.exists():
        with io.open(BANK_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {'version': 1, 'updated': '', 'questions': []}


def inject_snapshot(bank: dict) -> None:
    """
    把题库数据写入 index.html 的 <script id="bank-snapshot"> 里，
    直接双击打开网页也能看到全部题目（file:// 下 fetch 会被浏览器拦截）
    """
    text = io.open(INDEX_FILE, encoding='utf-8').read()
    start_tag = f'<script id="{SNAPSHOT_ID}" type="application/json">'
    start = text.index(start_tag) + len(start_tag)
    end = text.index('</script>', start)

    payload = json.dumps(bank, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    text = text[:start] + payload + text[end:]
    io.open(INDEX_FILE, 'w', encoding='utf-8', newline='\n').write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description='题库网页工具')
    parser.add_argument('--open', action='store_true', help='生成后用浏览器打开 index.html')
    parser.add_argument('--empty', action='store_true', help='写入空快照（不读取题库文件）')
    args = parser.parse_args()

    bank = {'version': 1, 'updated': '', 'questions': []} if args.empty else load_bank()
    inject_snapshot(bank)
    count = len(bank.get('questions', []))
    print(f'已写入快照 {count} 条: {INDEX_FILE}')

    if args.open:
        webbrowser.open(INDEX_FILE.resolve().as_uri())
    return 0


if __name__ == '__main__':
    sys.exit(main())
