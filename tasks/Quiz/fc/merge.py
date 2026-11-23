# -*- coding: utf-8 -*-
"""
题库合并逻辑（函数计算版，纯标准库）

从 tasks/Quiz/bank_sync.py + tasks/Quiz/question_bank.py 抽出，
去掉 module.logger / pydantic 等依赖，方便直接打包进函数计算。
合并规则与原实现保持一致。
"""
import re
from datetime import datetime
from difflib import SequenceMatcher
from hashlib import md5
from copy import deepcopy

# 与主项目一致
SAME_TEXT_THRESHOLD = 0.8
OPTION_THRESHOLD = 0.8
DROP_THRESHOLD = 0.5
PUNCTUATION = re.compile(r'[\s，。？！、：；·—…“”‘’（）()【】\[\]"\'？！,.:;!?\-_/\\|]')
ID_RE = re.compile(r'^q[0-9a-f]{9}$')
MAX_Q_LEN = 500
MAX_OPTION_LEN = 300
MAX_OPTIONS = 5
MAX_WRONG = 10
SOURCES = ('manual', 'llm', 'pending')


# ---------------------------------------------------------------- 文本工具
def normalize(text: str) -> str:
    return PUNCTUATION.sub('', text or '').lower()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def option_texts(options) -> list:
    if not options:
        return []
    if isinstance(options, str):
        values = [options]
    elif isinstance(options, dict):
        values = [options.get(k, '') for k in sorted(options)]
    else:
        values = list(options)
    return [str(v).strip() for v in values if v and str(v).strip()]


def option_map(options) -> dict:
    return {chr(ord('A') + i): text for i, text in enumerate(option_texts(options))}


def option_similarity(a: str, b: str) -> float:
    return similarity(a, b)


def best_option(options: dict, answer_text: str) -> tuple:
    target = normalize(answer_text)
    if not target or not options:
        return '', 0.0
    best, best_score = '', 0.0
    for letter, text in options.items():
        score = option_similarity(text, answer_text)
        if score > best_score:
            best, best_score = letter, score
    return best, best_score


def find_excluded(answer_text: str, wrong: list) -> str:
    if not answer_text or not wrong:
        return ''
    target = normalize(answer_text)
    best, best_score = '', 0.0
    for item in wrong:
        score = similarity(target, normalize(item))
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= OPTION_THRESHOLD else ''


# ---------------------------------------------------------------- 合并
def _same_text(a: str, b: str) -> bool:
    return similarity(a, b) >= SAME_TEXT_THRESHOLD


def _answer_rank(record: dict) -> tuple:
    return (1 if record.get('verified') else 0,
            int(record.get('correct', 0) or 0),
            str(record.get('updated', '')))


def _source_rank(source: str) -> int:
    return {'manual': 3, 'llm': 2, 'pending': 1}.get(source or '', 0)


def _merge_record(target: dict, other: dict) -> tuple:
    changed = False
    conflict = False

    if len([o for o in other.get('options', []) if o]) > len([o for o in target.get('options', []) if o]):
        target['options'] = list(other['options'])
        changed = True

    t_ans, o_ans = target.get('answer', ''), other.get('answer', '')
    t_verified, o_verified = bool(target.get('verified')), bool(other.get('verified'))

    if o_ans and not t_ans:
        target['answer'] = o_ans
        target['verified'] = o_verified
        changed = True
    elif t_ans and o_ans and not _same_text(t_ans, o_ans):
        other_wins = (o_verified and not t_verified) or (
                o_verified == t_verified and _answer_rank(other) > _answer_rank(target))
        if other_wins:
            loser, winner, winner_verified = t_ans, o_ans, o_verified
        else:
            loser, winner, winner_verified = o_ans, t_ans, t_verified
        if target['answer'] != winner:
            target['answer'] = winner
            changed = True
        if t_verified and o_verified:
            target['conflict'] = True
            conflict = True
            changed = True
        elif t_verified or o_verified:
            wrong = target.setdefault('wrong', [])
            if loser and loser not in wrong:
                wrong.append(loser)
                changed = True
        target['verified'] = winner_verified
    elif t_ans and o_ans:
        if o_verified and not t_verified:
            target['verified'] = True
            changed = True
        if _similarity_raw(t_ans, o_ans) < 1.0 and int(other.get('correct', 0) or 0) > int(target.get('correct', 0) or 0):
            target['answer'] = o_ans
            changed = True

    for word in other.get('wrong', []) or []:
        if word and word not in target.setdefault('wrong', []):
            target['wrong'].append(word)
            changed = True
    for key in ('hits', 'correct'):
        value = int(other.get(key, 0) or 0)
        if value > int(target.get(key, 0) or 0):
            target[key] = value
            changed = True
    if _source_rank(other.get('source')) > _source_rank(target.get('source')):
        target['source'] = other['source']
        changed = True
    if str(other.get('updated', '')) > str(target.get('updated', '')):
        target['updated'] = other['updated']
        changed = True
    return changed, conflict


def _similarity_raw(a: str, b: str) -> float:
    """原文相似度（不归一化），对齐 bank_sync 里 _similar 的行为"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _clean_record(record: dict) -> None:
    answer = record.get('answer', '')
    wrong = []
    for item in record.get('wrong', []) or []:
        if item and item not in wrong:
            wrong.append(item)

    if record.get('verified') and answer and any(_same_text(item, answer) for item in wrong):
        record['verified'] = False

    record['wrong'] = wrong
    record['hits'] = int(record.get('hits', 0) or 0)
    record['correct'] = int(record.get('correct', 0) or 0)
    if not answer:
        record['verified'] = False


def sanitize_questions(questions: list) -> dict:
    """与 QuestionBank.sanitize_questions 等价的自检"""
    stats = {'demoted': 0, 'dropped_wrong': 0}
    for record in questions:
        options = option_map(record.get('options'))
        answer = record.get('answer') or ''
        if answer and options:
            _, score = best_option(options, answer)
            if score < OPTION_THRESHOLD:
                record['verified'] = False
                record['correct'] = 0
                wrong = record.setdefault('wrong', [])
                if not find_excluded(answer, wrong):
                    wrong.append(answer)
                stats['demoted'] += 1
        wrong = record.get('wrong') or []
        if wrong and options:
            keep = [w for w in wrong
                    if best_option(options, w)[1] >= DROP_THRESHOLD
                    or option_similarity(w, answer) >= OPTION_THRESHOLD]
            if len(keep) != len(wrong):
                stats['dropped_wrong'] += len(wrong) - len(keep)
                record['wrong'] = keep
    return stats


def merge_questions(local: list, remote: list) -> tuple:
    """
    按 id 归并两份题目列表（与 tasks/Quiz/bank_sync.py 的 merge_questions 一致）
    :return: (合并后的题目列表, 统计)
    """
    merged = {}
    for item in local:
        if item.get('id') and item.get('q'):
            merged[item['id']] = deepcopy(item)

    stats = {'added': 0, 'updated': 0, 'conflict': 0, 'changed': False}
    for item in remote:
        if not item.get('id') or not item.get('q'):
            continue
        target = merged.get(item['id'])
        if target is None:
            merged[item['id']] = deepcopy(item)
            stats['added'] += 1
            stats['changed'] = True
            continue
        changed, conflict = _merge_record(target, deepcopy(item))
        if changed:
            stats['updated'] += 1
            stats['changed'] = True
        if conflict:
            stats['conflict'] += 1

    questions = list(merged.values())
    for record in questions:
        _clean_record(record)
    fixed = sanitize_questions(questions)
    stats['sanitized'] = sum(fixed.values())
    return questions, stats


# ---------------------------------------------------------------- 校验
def _to_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def clean_question(item) -> dict | None:
    """
    校验并规范化客户端提交的一条题目；不合格返回 None
    """
    if not isinstance(item, dict):
        return None
    q = str(item.get('q') or '').strip()
    if not q or len(q) > MAX_Q_LEN:
        return None

    options = []
    for value in option_texts(item.get('options'))[:MAX_OPTIONS]:
        if len(value) > MAX_OPTION_LEN:
            return None
        options.append(value)
    if len(options) < 2:
        return None

    answer = str(item.get('answer') or '').strip()
    if len(answer) > MAX_OPTION_LEN:
        answer = answer[:MAX_OPTION_LEN]

    wrong = []
    for value in option_texts(item.get('wrong'))[:MAX_WRONG]:
        if value not in wrong:
            wrong.append(value)

    question_id = str(item.get('id') or '')
    if not ID_RE.match(question_id):
        question_id = 'q' + md5(normalize(q).encode('utf-8')).hexdigest()[:9]

    source = str(item.get('source') or 'pending')
    if source not in SOURCES:
        source = 'pending'

    record = {
        'id': question_id,
        'q': q,
        'options': options,
        'answer': answer,
        'verified': bool(item.get('verified')),
        'wrong': wrong,
        'hits': _to_int(item.get('hits')),
        'correct': _to_int(item.get('correct')),
        'source': source,
        'updated': str(item.get('updated') or datetime.now().strftime('%Y-%m-%d'))[:32],
    }
    if item.get('conflict'):
        record['conflict'] = True
    return record


def build_bank(questions: list) -> dict:
    return {
        'version': 1,
        'updated': datetime.now().strftime('%Y-%m-%d'),
        'questions': questions,
    }
