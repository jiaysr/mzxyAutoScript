# -*- coding: utf-8 -*-
"""题库同步合并测试：同 id 但选项不同必须拆成两条（不连网络）"""
from tasks.Quiz import bank_sync
from tasks.Quiz.question_bank import QuestionBank

BASE_OPTIONS = ['甲', '乙', '丙']
OTHER_OPTIONS = ['丁', '戊', '己']


def _record(q, options, answer, verified=True, qid=None, **extra):
    record = {
        'id': qid or QuestionBank.make_id(q),
        'q': q,
        'options': list(options),
        'answer': answer,
        'verified': verified,
        'wrong': [],
        'hits': 1,
        'correct': 1,
        'source': 'llm',
        'updated': '2026-11-23',
    }
    record.update(extra)
    return record


def test_merge_same_id_different_options_splits_records():
    q = '题目'
    local = _record(q, BASE_OPTIONS, '甲')
    remote = _record(q, OTHER_OPTIONS, '丁')
    merged, stats = bank_sync.merge_questions([local], [remote])
    assert stats['split'] == 1
    assert len(merged) == 2
    by_id = {r['id']: r for r in merged}
    variant_id = QuestionBank.make_variant_id(q, BASE_OPTIONS)
    assert set(by_id) == {local['id'], variant_id}
    # 原 id 跟随远端那份，本地那份改挂变体 id；选项和答案各归各
    assert by_id[local['id']]['options'] == OTHER_OPTIONS
    assert by_id[local['id']]['answer'] == '丁'
    assert by_id[variant_id]['options'] == BASE_OPTIONS
    assert by_id[variant_id]['answer'] == '甲'
    assert by_id[variant_id]['verified'] is True


def test_merge_split_is_idempotent():
    q = '题目'
    local = _record(q, BASE_OPTIONS, '甲')
    remote = _record(q, OTHER_OPTIONS, '丁')
    merged, _ = bank_sync.merge_questions([local], [remote])
    merged2, stats2 = bank_sync.merge_questions(merged, [remote])
    assert stats2['split'] == 0
    assert len(merged2) == 2
    # 另一台机器拿着远端那份再合并拆分后的库：两条都在，不重复
    merged3, stats3 = bank_sync.merge_questions([remote], merged)
    assert len(merged3) == 2
    assert stats3['split'] == 0


def test_merge_same_options_different_order_is_not_conflict():
    q = '题目'
    merged, stats = bank_sync.merge_questions(
        [_record(q, ['甲', '乙'], '甲')], [_record(q, ['乙', '甲'], '甲')])
    assert stats['split'] == 0
    assert len(merged) == 1


def test_merge_empty_options_filled_without_split():
    local = _record('题目', [], '', verified=False)
    remote = _record('题目', BASE_OPTIONS, '甲')
    merged, stats = bank_sync.merge_questions([local], [remote])
    assert stats['split'] == 0
    assert len(merged) == 1
    assert merged[0]['options'] == BASE_OPTIONS
    assert merged[0]['answer'] == '甲'


def test_merge_existing_variant_takes_precedence():
    q = '题目'
    variant_id = QuestionBank.make_variant_id(q, BASE_OPTIONS)
    # 历史数据：同一份内容既挂在变体 id 上又挂在原 id 上，远端原 id 是另一组选项
    local = [_record(q, BASE_OPTIONS, '甲', qid=variant_id), _record(q, BASE_OPTIONS, '甲')]
    remote = [_record(q, OTHER_OPTIONS, '丁')]
    merged, stats = bank_sync.merge_questions(local, remote)
    assert stats['split'] == 1
    assert len(merged) == 2
    assert {r['id'] for r in merged} == {variant_id, QuestionBank.make_id(q)}
    by_id = {r['id']: r for r in merged}
    assert by_id[variant_id]['options'] == BASE_OPTIONS
    assert by_id[QuestionBank.make_id(q)]['options'] == OTHER_OPTIONS
