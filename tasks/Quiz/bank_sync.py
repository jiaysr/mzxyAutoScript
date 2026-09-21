# This Python file uses the following encoding: utf-8
"""
题库跨设备同步（git 交换仓库）

多台设备共用一份题库：
- 本机题库仍是 tasks/Quiz/bank/quiz_zh.json（index.html 编辑器照常用）
- 交换仓库（如 mzxy-quiz-db）里只放一份 quiz_zh.json，当作传输通道
- 答题前 pull：取仓库里的题库 -> 与本机按 id 合并 -> 写回本机
- 答题后 push：合并后的题库写进交换仓库 -> commit -> push（被拒就重新合并再推）
- git 永远不做 JSON 内容合并：每次都是「取远端 -> 结构化合并 -> 覆盖重写」，
  所以不会出现 JSON 冲突

合并规则（同一条题目 = 同一个 id = 归一化题干的 md5）：
- 答案：verified 优先 -> correct 次数多的优先 -> updated 晚的优先
- 一边 verified、另一边不是：verified 的答案为准，另一边的答案进 wrong
- 两边都 verified 且答案不同（且不是 OCR 写法差异）：标 conflict 并告警，人工核对
- 两边都没 verified：按上面的优先级选一份，不动 wrong（两个都只是猜测）
- wrong 取并集（剔除与最终答案相似的项），hits/correct 取较大的一份（见 _merge_record 注释），
  选项列表取更完整的一份，source 人工(manual) 优先
- 合并是幂等的：同一份题库反复 pull/push 不会改变内容，也不会让计数膨胀
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

if __package__ in (None, ''):  # 支持 python tasks/Quiz/bank_sync.py 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tasks.Quiz.question_bank import QuestionBank
else:
    from tasks.Quiz.question_bank import QuestionBank

from module.logger import logger

# 交换仓库里的题库文件名（与本机题库同名）
BANK_NAME = 'quiz_zh.json'
# 交换仓库默认放在项目同级目录（如 D:/project/mzxy/mzxyAutoScript -> D:/project/mzxy/quiz-db）
# 各机器目录结构一致时，题库同步不需要任何配置
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BANK_REPO_DIR = (PROJECT_ROOT.parent / 'quiz-db').as_posix()
# 默认的题库交换仓库地址（可配置覆盖）
DEFAULT_BANK_REPO_URL = 'git@github.com:jiaysr/mzxy-quiz-db.git'
# 交换仓库分支
BANK_BRANCH = 'main'
# 认为答案是「同一个选项的 OCR 写法差异」的相似度
SAME_TEXT_THRESHOLD = 0.8
# 推送被拒后的重试次数
PUSH_RETRY = 3


# ---------------------------------------------------------------- 读写
def read_questions(path: str) -> list:
    """
    读题库文件里的题目列表，文件不存在返回空列表
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return json.load(f).get('questions', [])


# ---------------------------------------------------------------- 合并
def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, QuestionBank.normalize(a), QuestionBank.normalize(b)).ratio()


def _same_text(a: str, b: str) -> bool:
    return _similar(a, b) >= SAME_TEXT_THRESHOLD


def _answer_rank(record: dict) -> tuple:
    """
    答案的可信度排序：verified -> correct 次数 -> updated 日期
    """
    return (1 if record.get('verified') else 0,
            int(record.get('correct', 0) or 0),
            str(record.get('updated', '')))


def _source_rank(source: str) -> int:
    return {'manual': 3, 'llm': 2, 'pending': 1}.get(source or '', 0)


def _merge_record(target: dict, other: dict) -> tuple:
    """
    把 other 合并进 target（原地修改）
    :return: (是否改动, 是否答案冲突)
    """
    changed = False
    conflict = False

    # 选项列表取更完整的一份
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
        # 两个答案不一样：按可信度选一份
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
            # 两边都被游戏确认过：至少有一边是错的，标出来等人工核对
            target['conflict'] = True
            conflict = True
            changed = True
        elif t_verified or o_verified:
            # 只有一边被确认过，另一边的答案就是错的
            wrong = target.setdefault('wrong', [])
            if loser and loser not in wrong:
                wrong.append(loser)
                changed = True
        target['verified'] = winner_verified
    elif t_ans and o_ans:
        # 同一个答案：verified 取或，文字取 correct 次数多的那一份（OCR 写法差异）
        if o_verified and not t_verified:
            target['verified'] = True
            changed = True
        if _similar(t_ans, o_ans) < 1.0 and int(other.get('correct', 0) or 0) > int(target.get('correct', 0) or 0):
            target['answer'] = o_ans
            changed = True

    # wrong 并集
    for word in other.get('wrong', []) or []:
        if word and word not in target.setdefault('wrong', []):
            target['wrong'].append(word)
            changed = True
    # 计数取较大的一份（不做跨设备累加：同一份数据会被反复合并，累加会无限膨胀，
    # 计数只用来在答案冲突时比较可信度）
    for key in ('hits', 'correct'):
        value = int(other.get(key, 0) or 0)
        if value > int(target.get(key, 0) or 0):
            target[key] = value
            changed = True
    # source 人工优先，updated 取较晚
    if _source_rank(other.get('source')) > _source_rank(target.get('source')):
        target['source'] = other['source']
        changed = True
    if str(other.get('updated', '')) > str(target.get('updated', '')):
        target['updated'] = other['updated']
        changed = True
    return changed, conflict


def _clean_record(record: dict) -> None:
    """
    合并收尾：wrong 去掉与答案相同的项，补齐计数字段
    """
    answer = record.get('answer', '')
    wrong = []
    for item in record.get('wrong', []) or []:
        if item and not _same_text(item, answer) and item not in wrong:
            wrong.append(item)
    record['wrong'] = wrong
    record['hits'] = int(record.get('hits', 0) or 0)
    record['correct'] = int(record.get('correct', 0) or 0)
    if not answer:
        record['verified'] = False


def merge_questions(local: list, remote: list) -> tuple:
    """
    按 id 归并两份题目列表
    :return: (合并后的题目列表, 统计 {'added', 'updated', 'conflict', 'changed'})
    """
    merged = {}
    for item in local:
        if item.get('id') and item.get('q'):
            merged[item['id']] = copy.deepcopy(item)

    stats = {'added': 0, 'updated': 0, 'conflict': 0, 'changed': False}
    for item in remote:
        if not item.get('id') or not item.get('q'):
            continue
        target = merged.get(item['id'])
        if target is None:
            merged[item['id']] = copy.deepcopy(item)
            stats['added'] += 1
            stats['changed'] = True
            continue
        changed, conflict = _merge_record(target, copy.deepcopy(item))
        if changed:
            stats['updated'] += 1
            stats['changed'] = True
        if conflict:
            stats['conflict'] += 1

    questions = list(merged.values())
    for record in questions:
        _clean_record(record)
    return questions, stats


# ---------------------------------------------------------------- git
def _git(repo_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(('git', '-C', repo_dir) + args,
                          capture_output=True, encoding='utf-8', errors='replace')


def _git_ok(repo_dir: str, *args: str) -> bool:
    return _git(repo_dir, *args).returncode == 0


def _ensure_repo(repo_dir: str, url: str = '') -> bool:
    """
    交换仓库目录不可用时按 url 自动 clone
    """
    if not repo_dir:
        return False
    if os.path.isdir(os.path.join(repo_dir, '.git')):
        return True
    if os.path.exists(os.path.join(repo_dir, BANK_NAME)):
        logger.warning(f'题库仓库目录没初始化 git: {repo_dir}')
        return False
    if not url:
        logger.warning(f'题库仓库不存在且没有配置地址: {repo_dir}')
        return False

    folder = os.path.dirname(os.path.abspath(repo_dir))
    os.makedirs(folder, exist_ok=True)
    logger.info(f'Clone question bank repo: {url} -> {repo_dir}')
    result = subprocess.run(('git', 'clone', url, repo_dir),
                            capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        logger.warning(f'clone 题库仓库失败: {result.stderr.strip()}')
        return False
    return True


def _remote_branch_exists(repo_dir: str, branch: str) -> bool:
    return _git_ok(repo_dir, 'rev-parse', '--verify', f'refs/remotes/origin/{branch}')


def _remote_questions(repo_dir: str, branch: str) -> list:
    """
    读交换仓库里的题目列表，仓库里还没有这份文件返回 None
    """
    if not _remote_branch_exists(repo_dir, branch):
        return None
    result = _git(repo_dir, 'show', f'origin/{branch}:{BANK_NAME}')
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get('questions', [])
    except json.JSONDecodeError as e:
        logger.warning(f'题库仓库里的 {BANK_NAME} 解析失败: {e}')
        return None


def _reset_repo(repo_dir: str, branch: str) -> None:
    """
    让交换仓库与远端对齐（内容以合并结果为准，所以可以直接丢弃本地差异）
    """
    if not _git_ok(repo_dir, 'rev-parse', '--verify', 'HEAD'):
        return  # 空仓库还没有提交，不用对齐
    _git_ok(repo_dir, 'checkout', '-B', branch)
    if _remote_branch_exists(repo_dir, branch):
        _git_ok(repo_dir, 'reset', '--hard', f'origin/{branch}')


# ---------------------------------------------------------------- 同步
def pull(bank_path: str, repo_dir: str = '', url: str = '', branch: str = BANK_BRANCH) -> dict:
    """
    取交换仓库的题库并与本机合并（合并结果写回本机题库）
    :return: 合并统计，未同步返回 {}
    """
    if not _ensure_repo(repo_dir, url or DEFAULT_BANK_REPO_URL):
        return {}
    _git_ok(repo_dir, 'fetch', 'origin', branch)
    remote = _remote_questions(repo_dir, branch)
    if not remote:
        logger.info('题库仓库里还没有题库，跳过合并')
        return {}

    local = read_questions(bank_path)
    merged, stats = merge_questions(local, remote)
    logger.attr('Question bank pull',
                f'{len(local)} + {len(remote)} -> {len(merged)} '
                f'(added {stats["added"]}, updated {stats["updated"]}, conflict {stats["conflict"]})')
    if stats['conflict']:
        logger.warning(f'题库有 {stats["conflict"]} 条答案冲突，请在题库网页工具里人工核对')
    if stats['changed']:
        QuestionBank.dump(bank_path, merged)
    return stats


def push(bank_path: str, repo_dir: str = '', url: str = '', branch: str = BANK_BRANCH,
         device: str = '', retry: int = PUSH_RETRY) -> bool:
    """
    把本机题库合并进交换仓库并推送（被拒就重新合并再推）
    :return: 是否推送成功
    """
    if not _ensure_repo(repo_dir, url or DEFAULT_BANK_REPO_URL):
        return False
    local = read_questions(bank_path)
    if not local:
        logger.warning('本机题库为空，跳过推送')
        return False

    for attempt in range(retry):
        _git_ok(repo_dir, 'fetch', 'origin', branch)
        remote = _remote_questions(repo_dir, branch) or []
        merged, stats = merge_questions(local, remote)
        if remote and stats['changed']:
            # 仓库里别人新加的题也一并写回本机
            QuestionBank.dump(bank_path, merged)

        _reset_repo(repo_dir, branch)
        QuestionBank.dump(os.path.join(repo_dir, BANK_NAME), merged)
        _git_ok(repo_dir, 'add', BANK_NAME)
        _git_ok(repo_dir, 'commit', '-m', f'sync({device or "oas"}): {len(merged)} questions')
        result = _git(repo_dir, 'push', 'origin', f'HEAD:{branch}')
        if result.returncode == 0:
            logger.attr('Question bank push',
                        f'{len(merged)} questions (added {stats["added"]}, '
                        f'updated {stats["updated"]}, conflict {stats["conflict"]})')
            return True
        logger.warning(f'题库推送被拒（第 {attempt + 1}/{retry} 次）: {result.stderr.strip()[-200:]}')
        time.sleep(1 + attempt)

    logger.warning('题库推送失败，本机题库不受影响，下次答题结束后会重新同步')
    return False


# ---------------------------------------------------------------- CLI
def main() -> int:
    parser = argparse.ArgumentParser(description='题库跨设备同步（git 交换仓库）')
    sub = parser.add_subparsers(dest='command', required=True)

    merge = sub.add_parser('merge', help='合并两份题库文件（按 id 归并，verified 优先）')
    merge.add_argument('local', help='本机题库 JSON')
    merge.add_argument('remote', help='远端题库 JSON')
    merge.add_argument('-o', '--output', default='', help='输出文件，不填则覆盖 local')

    for name in ('pull', 'push'):
        item = sub.add_parser(name, help=f'{name} 交换仓库')
        item.add_argument('--bank', default=str(Path('tasks/Quiz/bank/quiz_zh.json')), help='本机题库路径')
        item.add_argument('--repo', default=DEFAULT_BANK_REPO_DIR, help='题库交换仓库目录')
        item.add_argument('--url', default=DEFAULT_BANK_REPO_URL, help='仓库地址（目录不存在时自动 clone）')
        if name == 'push':
            item.add_argument('--device', default='', help='设备名（写进提交信息）')

    args = parser.parse_args()
    if args.command == 'merge':
        questions, stats = merge_questions(read_questions(args.local), read_questions(args.remote))
        QuestionBank.dump(args.output or args.local, questions)
        print(f'合并完成 {len(questions)} 条: added {stats["added"]}, '
              f'updated {stats["updated"]}, conflict {stats["conflict"]}')
        return 0
    if args.command == 'pull':
        stats = pull(args.bank, args.repo, url=args.url)
        print(f'pull: {stats or "未同步"}')
        return 0
    ok = push(args.bank, args.repo, url=args.url, device=args.device)
    print(f'push: {"成功" if ok else "失败"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
