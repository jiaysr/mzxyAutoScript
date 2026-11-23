# -*- coding: utf-8 -*-
"""题库 Gitee 推送测试（不连网络）"""
import base64
import json
from types import SimpleNamespace

from tasks.Quiz import bank_sync


class FakeResponse:
    def __init__(self, status_code, data=None, text=''):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = text or json.dumps(self._data, ensure_ascii=False)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bank_sync.requests.HTTPError(
                f'HTTP {self.status_code}', response=self)


def _question(answer='A选项', verified=True):
    return {
        'id': 'qabcdef123',
        'q': '测试题',
        'options': ['A选项', 'B选项'],
        'answer': answer,
        'verified': verified,
        'wrong': [],
        'hits': 1,
        'correct': 1,
        'source': 'llm',
        'updated': '2026-11-23',
    }


def _bank_file(tmp_path, questions):
    path = tmp_path / 'quiz_zh.json'
    path.write_text(json.dumps({
        'version': 1,
        'questions': questions,
    }, ensure_ascii=False), encoding='utf-8')
    return str(path)


def _remote_response(questions, sha='sha-old'):
    bank = {'version': 1, 'questions': questions}
    content = base64.b64encode(
        json.dumps(bank, ensure_ascii=False).encode('utf-8')).decode('ascii')
    return FakeResponse(200, {'content': content, 'sha': sha})


def test_gitee_config_is_hardcoded():
    assert bank_sync.DEFAULT_BANK_REPO_URL == 'https://gitee.com/jiysr/mzxy-quiz-db.git'
    assert bank_sync.GITEE_BRANCH == 'main'
    assert bank_sync.GITEE_TOKEN


def test_push_skips_when_content_unchanged(monkeypatch, tmp_path):
    question = _question()
    put_calls = []

    monkeypatch.setattr(bank_sync.requests, 'get',
                        lambda *args, **kwargs: _remote_response([question]))
    monkeypatch.setattr(bank_sync.requests, 'put',
                        lambda *args, **kwargs: put_calls.append(1))

    assert bank_sync.push(_bank_file(tmp_path, [question])) is True
    assert put_calls == []


def test_push_merges_and_puts_with_hardcoded_token(monkeypatch, tmp_path):
    local = _question(answer='A选项', verified=True)
    remote = _question(answer='B选项', verified=False)
    captured = {}

    monkeypatch.setattr(bank_sync.requests, 'get',
                        lambda *args, **kwargs: _remote_response([remote], sha='sha-old'))

    def fake_put(url, **kwargs):
        captured['url'] = url
        captured.update(kwargs)
        return FakeResponse(200, {})

    monkeypatch.setattr(bank_sync.requests, 'put', fake_put)

    assert bank_sync.push(_bank_file(tmp_path, [local]), device='oas1') is True
    assert captured['url'].startswith(f'{bank_sync.GITEE_API}/repos/')
    assert captured['params']['access_token'] == bank_sync.GITEE_TOKEN
    payload = captured['json']
    assert payload['sha'] == 'sha-old'
    assert payload['branch'] == 'main'
    assert payload['message'].startswith('sync(oas1):')
    merged = json.loads(base64.b64decode(payload['content']).decode('utf-8'))
    assert merged['questions'][0]['answer'] == 'A选项'


def test_push_retries_on_conflict(monkeypatch, tmp_path):
    local = _question(answer='A选项', verified=True)
    remote = _question(answer='B选项', verified=False)
    put_calls = []

    monkeypatch.setattr(bank_sync.requests, 'get',
                        lambda *args, **kwargs: _remote_response([remote]))

    def fake_put(*args, **kwargs):
        put_calls.append(1)
        if len(put_calls) == 1:
            return FakeResponse(409, {'message': 'sha does not match'})
        return FakeResponse(200, {})

    monkeypatch.setattr(bank_sync.requests, 'put', fake_put)
    monkeypatch.setattr(bank_sync.time, 'sleep', lambda seconds: None)

    assert bank_sync.push(_bank_file(tmp_path, [local]), retry=3) is True
    assert len(put_calls) == 2


def test_push_stops_on_forbidden(monkeypatch, tmp_path):
    local = _question(answer='A选项', verified=True)
    remote = _question(answer='B选项', verified=False)
    put_calls = []

    monkeypatch.setattr(bank_sync.requests, 'get',
                        lambda *args, **kwargs: _remote_response([remote]))
    monkeypatch.setattr(bank_sync.requests, 'put',
                        lambda *args, **kwargs: put_calls.append(1) or FakeResponse(403, {}))

    assert bank_sync.push(_bank_file(tmp_path, [local]), retry=3) is False
    assert len(put_calls) == 1


def test_ensure_repo_switches_remote_to_gitee(monkeypatch, tmp_path):
    repo = tmp_path / 'quiz-db'
    (repo / '.git').mkdir(parents=True)
    calls = []

    def fake_git(repo_dir, *args):
        calls.append(args)
        if args[:2] == ('remote', 'get-url'):
            return SimpleNamespace(returncode=0,
                                   stdout='git@github.com:jiaysr/mzxy-quiz-db.git\n',
                                   stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(bank_sync, '_git', fake_git)
    assert bank_sync._ensure_repo(str(repo)) is True
    assert ('remote', 'set-url', 'origin', bank_sync.DEFAULT_BANK_REPO_URL) in calls
