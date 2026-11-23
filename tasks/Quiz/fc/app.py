# -*- coding: utf-8 -*-
"""
题库共享 API（阿里云函数计算 Web 函数）

环境变量：
    GITHUB_TOKEN   必填，Fine-grained PAT（Contents: Read and write）
    GITHUB_REPO    必填，owner/repo，如 jiaysr/mzxy-quiz-db
    GITHUB_BRANCH  可选，默认 main
    BANK_PATH      可选，默认 quiz_zh.json
    BANK_API_KEY   必填，客户端提交时放在 X-Api-Key 头里

接口：
    GET  /health          健康检查
    POST /quiz/submit     提交本机题库（{"questions": [...]}），服务端合并后提交 GitHub

客户端只负责 pull（GitHub raw），push 走这个接口。
"""
import base64
import hmac
import json
import os
import time
from datetime import datetime
from urllib import error, parse, request

from flask import Flask, jsonify, request as flask_request

from merge import build_bank, clean_question, merge_questions

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024  # 单次提交上限 4MB

GITHUB_API = 'https://api.github.com'
GITHUB_TIMEOUT = 20
PUT_RETRY = 3


# ---------------------------------------------------------------- 配置
def _env(name: str, default: str = '') -> str:
    return str(os.environ.get(name, default) or '').strip()


def _github_repo() -> str:
    return _env('GITHUB_REPO')


def _github_branch() -> str:
    return _env('GITHUB_BRANCH', 'main') or 'main'


def _bank_path() -> str:
    return _env('BANK_PATH', 'quiz_zh.json') or 'quiz_zh.json'


def _check_api_key() -> bool:
    expected = _env('BANK_API_KEY')
    if not expected:
        return False
    got = str(flask_request.headers.get('X-Api-Key') or '')
    return hmac.compare_digest(got, expected)


# ---------------------------------------------------------------- GitHub
def _github_headers() -> dict:
    token = _env('GITHUB_TOKEN')
    if not token:
        raise RuntimeError('GITHUB_TOKEN 未配置')
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'mzxy-quiz-bank-fc',
    }


def _contents_url() -> str:
    repo = _github_repo()
    if not repo:
        raise RuntimeError('GITHUB_REPO 未配置')
    path = parse.quote(_bank_path(), safe='/')
    return f'{GITHUB_API}/repos/{repo}/contents/{path}'


def _read_http_error(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode('utf-8', errors='replace')
        data = json.loads(body)
        return str(data.get('message') or body)[:300]
    except Exception:  # noqa: BLE001
        return str(exc)[:300]


def _canonical_questions(questions: list) -> str:
    """按 id 排序后的规范 JSON，用来判断合并结果是否真的发生了变化"""
    return json.dumps(sorted(questions, key=lambda item: str(item.get('id') or '')),
                      ensure_ascii=False, sort_keys=True)


def _github_get_bank() -> tuple:
    """
    读取仓库里的题库
    :return: (bank dict, sha or None)；文件不存在时返回空题库和 None
    """
    url = f'{_contents_url()}?ref={parse.quote(_github_branch())}'
    req = request.Request(url, headers=_github_headers(), method='GET')
    try:
        with request.urlopen(req, timeout=GITHUB_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except error.HTTPError as exc:
        if exc.code == 404:
            return build_bank([]), None
        raise

    content = data.get('content') or ''
    if not content:
        raise RuntimeError('题库文件为空或过大（Contents API 不返回内容）')
    raw = base64.b64decode(content).decode('utf-8')
    bank = json.loads(raw)
    if not isinstance(bank, dict):
        bank = {'questions': bank}
    bank.setdefault('questions', [])
    return bank, data.get('sha')


def _github_put_bank(bank: dict, sha: str | None) -> dict:
    payload = {
        'message': f'sync(api): {len(bank.get("questions", []))} questions',
        'content': base64.b64encode(
            json.dumps(bank, ensure_ascii=False, indent=2).encode('utf-8')).decode('ascii'),
        'branch': _github_branch(),
    }
    if sha:
        payload['sha'] = sha
    data = json.dumps(payload).encode('utf-8')
    headers = _github_headers()
    headers['Content-Type'] = 'application/json'
    req = request.Request(_contents_url(), data=data, headers=headers, method='PUT')
    with request.urlopen(req, timeout=GITHUB_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))


# ---------------------------------------------------------------- 接口
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'ok': True,
        'repo': _github_repo(),
        'branch': _github_branch(),
        'bank': _bank_path(),
    })


@app.route('/', methods=['GET'])
def index():
    return health()


@app.route('/quiz/submit', methods=['POST'])
def submit():
    if not _check_api_key():
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    try:
        payload = flask_request.get_json(force=True, silent=False)
    except Exception:  # noqa: BLE001
        return jsonify({'ok': False, 'error': 'invalid json'}), 400

    items = payload.get('questions') if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        return jsonify({'ok': False, 'error': 'questions 必须是非空数组'}), 400
    if len(items) > 20000:
        return jsonify({'ok': False, 'error': '单次提交题目过多'}), 413

    submitted = []
    skipped = 0
    for item in items:
        record = clean_question(item)
        if record is None:
            skipped += 1
        else:
            submitted.append(record)
    if not submitted:
        return jsonify({'ok': False, 'error': '没有合法题目', 'skipped': skipped}), 400

    last_error = ''
    for attempt in range(PUT_RETRY):
        try:
            remote_bank, sha = _github_get_bank()
            remote_questions = remote_bank.get('questions', [])
            merged, stats = merge_questions(remote_questions, submitted)
            # stats['changed'] 可能因为合并过程中间值变化而误报；以最终内容为准
            changed = _canonical_questions(remote_questions) != _canonical_questions(merged)
            if changed:
                bank = build_bank(merged)
                _github_put_bank(bank, sha)
            else:
                stats['added'] = 0
                stats['updated'] = 0
                stats['changed'] = False
            return jsonify({
                'ok': True,
                'changed': changed,
                'total': len(merged),
                'submitted': len(submitted),
                'skipped': skipped,
                'added': stats.get('added', 0),
                'updated': stats.get('updated', 0),
                'conflict': stats.get('conflict', 0),
                'sanitized': stats.get('sanitized', 0),
                'attempt': attempt + 1,
            })
        except error.HTTPError as exc:
            last_error = f'HTTP {exc.code}: {_read_http_error(exc)}'
            # 409/422：提交期间别人改了文件，重新拉取合并再试
            if exc.code in (409, 422) and attempt + 1 < PUT_RETRY:
                time.sleep(1 + attempt)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:300]
            break

    return jsonify({'ok': False, 'error': last_error or 'unknown error'}), 500


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({'ok': False, 'error': '请求体过大'}), 413


if __name__ == '__main__':
    port = int(os.environ.get('PORT') or 9000)
    app.run(host='0.0.0.0', port=port)
