# -*- coding: utf-8 -*-
"""题库写入身份测试：题干和选项都相同才算同一条（不连网络）"""
import json

from tasks.Quiz.question_bank import QuestionBank

BASE_OPTIONS = ['甲', '乙', '丙']
OTHER_OPTIONS = ['丁', '戊', '己']


def _record(q, options, answer='', verified=False, wrong=None, **extra):
    record = {
        'id': QuestionBank.make_id(q),
        'q': q,
        'options': list(options),
        'answer': answer,
        'verified': verified,
        'wrong': list(wrong or []),
        'hits': 0,
        'correct': 0,
        'source': 'test',
        'updated': '2026-11-23',
    }
    record.update(extra)
    return record


def _bank(tmp_path, questions):
    path = tmp_path / 'quiz_zh.json'
    path.write_text(json.dumps({'version': 1, 'questions': questions}, ensure_ascii=False),
                    encoding='utf-8')
    return QuestionBank(str(path), str(tmp_path / 'unknown.jsonl')).load()


def test_exact_record_requires_identical_options(tmp_path):
    bank = _bank(tmp_path, [_record('题目', BASE_OPTIONS, '甲', True)])
    # 选项顺序不同仍算同一条
    assert bank.exact_record('题目', {'C': '丙', 'A': '甲', 'B': '乙'}) is not None
    # 少一个/换一个/没读出来，都不是同一条：写反馈不能写到别的选项组上
    assert bank.exact_record('题目', {'A': '甲', 'B': '乙'}) is None
    assert bank.exact_record('题目', {'A': '甲', 'B': '丁', 'C': '丙'}) is None
    assert bank.exact_record('题目', None) is None


def test_same_options_ignores_order(tmp_path):
    assert QuestionBank.same_options(['甲', '乙'], {'C': '乙', 'A': '甲'})
    assert not QuestionBank.same_options(['甲', '乙'], ['甲', '乙', '丙'])
    assert QuestionBank.same_options([], None)
    assert not QuestionBank.same_options([], ['甲'])


def test_make_variant_id_stable_across_option_order(tmp_path):
    a = QuestionBank.make_variant_id('题目', ['甲', '乙', '丙'])
    b = QuestionBank.make_variant_id('题目', {'A': '甲', 'B': '乙', 'C': '丙'})
    c = QuestionBank.make_variant_id('题目', ['丙', '甲', '乙'])
    assert a == b == c
    assert a != QuestionBank.make_variant_id('题目', OTHER_OPTIONS)
    assert a != QuestionBank.make_id('题目')


def test_ensure_entry_creates_variant_for_different_options(tmp_path):
    bank = _bank(tmp_path, [])
    first = bank.ensure_entry('题目', {'A': '甲', 'B': '乙', 'C': '丙'})
    assert first['id'] == QuestionBank.make_id('题目')
    variant = bank.ensure_entry('题目', {'A': '丁', 'B': '戊', 'C': '己'})
    assert variant['id'] == QuestionBank.make_variant_id('题目', OTHER_OPTIONS)
    assert variant['id'] != first['id']
    assert len(bank.questions) == 2
    # 同样的选项再来一次：复用变体，不重复建条目
    assert bank.ensure_entry('题目', {'C': '己', 'B': '戊', 'A': '丁'}) is variant
    assert len(bank.questions) == 2
    # 同选项不同顺序也命中第一条
    assert bank.ensure_entry('题目', {'A': '丙', 'B': '乙', 'C': '甲'}) is first


def test_upsert_and_feedback_stay_in_their_option_group(tmp_path):
    original = _record('题目', BASE_OPTIONS, '甲', True, correct=2)
    bank = _bank(tmp_path, [original])
    variant = bank.ensure_entry('题目', {'A': '丁', 'B': '戊', 'C': '己'})
    bank.upsert('题目', {'A': '丁', 'B': '戊', 'C': '己'}, '丁', record=variant)
    assert variant['answer'] == '丁'
    assert original['answer'] == '甲'

    # 拿着别的选项组的 record 来写反馈：必须落到同选项的变体上
    bank.update_by_feedback('题目', '戊', 'wrong', record=original,
                            options={'A': '丁', 'B': '戊', 'C': '己'})
    assert original['hits'] == 0
    assert original['verified'] is True
    assert original['answer'] == '甲'
    assert variant['hits'] == 1
    assert variant['verified'] is False
    assert any(QuestionBank.option_similarity(w, '戊') >= 0.8 for w in variant['wrong'])
