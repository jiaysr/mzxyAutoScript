# -*- coding: utf-8 -*-
"""题库 API 推送测试（不连网络）"""
import json

from tasks.Quiz import bank_sync


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._data


def _bank_file(tmp_path):
    path = tmp_path / 'quiz_zh.json'
    path.write_text(json.dumps({
        'version': 1,
        'questions': [
            {
                'id': 'qabcdef123',
                'q': '测试题',
                'options': ['A选项', 'B选项'],
                'answer': 'A选项',
                'verified': True,
                'wrong': ['B选项'],
                'hits': 1,
                'correct': 1,
                'source': 'llm',
                'updated': '2026-11-23',
            }
        ],
    }, ensure_ascii=False), encoding='utf-8')
    return str(path)


def test_push_uses_builtin_api_key(monkeypatch, tmp_path):
    calls = {}

    def fake_post(url, **kwargs):
        calls.update(kwargs)
        return FakeResponse(200, {
            'ok': True, 'changed': False, 'total': 1,
            'added': 0, 'updated': 0, 'conflict': 0,
        })

    monkeypatch.setattr(bank_sync.requests, 'post', fake_post)
    assert bank_sync.push(_bank_file(tmp_path)) is True
    assert calls['headers']['X-Api-Key'] == bank_sync.BANK_API_KEY


def test_push_skips_when_builtin_key_missing(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError('没有可用 Key 时不应该发起任何推送')

    monkeypatch.setattr(bank_sync, 'BANK_API_KEY', '')
    monkeypatch.setattr(bank_sync.requests, 'post', fail)
    monkeypatch.setattr(bank_sync, '_git', fail)
    assert bank_sync.push(_bank_file(tmp_path)) is False


def test_push_posts_to_hardcoded_api(monkeypatch, tmp_path):
    calls = {}

    def fake_post(url, **kwargs):
        calls['url'] = url
        calls.update(kwargs)
        return FakeResponse(200, {
            'ok': True, 'changed': True, 'total': 1,
            'added': 0, 'updated': 0, 'conflict': 0,
        })

    monkeypatch.setattr(bank_sync.requests, 'post', fake_post)
    assert bank_sync.push(_bank_file(tmp_path), api_key='secret', device='oas1') is True

    assert calls['url'] == bank_sync.BANK_API_URL + bank_sync.BANK_API_SUBMIT_PATH
    assert calls['headers']['X-Api-Key'] == 'secret'
    assert len(calls['json']['questions']) == 1
    assert calls['json']['device'] == 'oas1'


def test_push_does_not_retry_on_unauthorized(monkeypatch, tmp_path):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(401, {'ok': False, 'error': 'unauthorized'})

    monkeypatch.setattr(bank_sync.requests, 'post', fake_post)
    monkeypatch.setattr(bank_sync.time, 'sleep', lambda seconds: None)
    assert bank_sync.push(_bank_file(tmp_path), api_key='bad', retry=3) is False
    assert len(calls) == 1


def test_push_retries_on_network_error(monkeypatch, tmp_path):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        if len(calls) < 2:
            raise bank_sync.requests.ConnectionError('boom')
        return FakeResponse(200, {'ok': True, 'changed': False, 'total': 1})

    monkeypatch.setattr(bank_sync.requests, 'post', fake_post)
    monkeypatch.setattr(bank_sync.time, 'sleep', lambda seconds: None)
    assert bank_sync.push(_bank_file(tmp_path), api_key='secret', retry=3) is True
    assert len(calls) == 2
