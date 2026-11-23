# This Python file uses the following encoding: utf-8
"""
题库跨设备同步（git 拉取 + Gitee API 推送）

多台设备共用一份题库：
- 本机题库仍是 tasks/Quiz/bank/quiz_zh.json（index.html 编辑器照常用）
- 题库交换仓库在 Gitee（`DEFAULT_BANK_REPO_URL`），拉取和推送都走这里
- 答题前 pull：从 Gitee 仓库取题库 -> 与本机按 id 合并 -> 写回本机
- 答题后 push：通过 Gitee OpenAPI 读取远端 -> 合并 -> 提交回仓库；
  Gitee 令牌写死在 `GITEE_TOKEN`，用户不需要配置
- 合并是幂等的：同一份题库反复 pull/push 不会改变内容，也不会让计数膨胀

合并规则（同题干同选项 = 同一个 id；同题干不同选项使用变体 id）：
- 答案：verified 优先 -> correct 次数多的优先 -> updated 晚的优先
- 一边 verified、另一边不是：verified 的答案为准，另一边的答案进 wrong
- 两边都 verified 且答案不同（且不是 OCR 写法差异）：标 conflict 并告警，人工核对
- 两边都没 verified：按上面的优先级选一份，不动 wrong（两个都只是猜测）
- wrong 取并集，hits/correct 取较大的一份（见 _merge_record 注释），
  选项列表取更完整的一份，source 人工(manual) 优先
- 合并收尾（_clean_record）：最终答案出现在排除表里 -> verified 降级（不让取并集冲掉降级）；
  未验证记录的 answer 允许同时留在排除表里（它就是上次试错的答案，删了就会重复答错）
- 合并是幂等的：同一份题库反复 pull/push 不会改变内容，也不会让计数膨胀
"""
import argparse
import base64
import copy
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

import requests

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
# 题库交换仓库（Gitee，国内访问更稳定；拉取和推送都走这里）
DEFAULT_BANK_REPO_URL = 'https://gitee.com/jiysr/mzxy-quiz-db.git'
# Gitee 仓库信息（写死，用户不需要配置）
GITEE_API = 'https://gitee.com/api/v5'
GITEE_OWNER = 'jiysr'
GITEE_REPO = 'mzxy-quiz-db'
GITEE_BRANCH = 'main'
GITEE_BANK_PATH = 'quiz_zh.json'
# Gitee 私人令牌（写死在客户端；拥有该仓库 Contents 写权限）
GITEE_TOKEN = 'aca6aa595e5b973da1923d2272532cb3'
# 交换仓库分支
BANK_BRANCH = GITEE_BRANCH
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


def _options_conflict(a, b) -> bool:
    """
    两组选项都读出来了但不相同 = 两道题（选项组不同）

    只有一边选项为空时不算冲突：那是 OCR 没读全，合并时应当补全
    """
    ta, tb = QuestionBank.option_texts(a), QuestionBank.option_texts(b)
    return bool(ta) and bool(tb) and not QuestionBank.same_options(ta, tb)


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
    把 other 合并进 target（原地修改，调用方保证两条记录选项相同）
    :return: (是否改动, 是否答案冲突)
    """
    if _options_conflict(target.get('options'), other.get('options')):
        # 同 id 但选项不同：这是两道题，merge_questions 会把另一份拆成变体 id
        return False, False

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
    合并收尾：整理排除表、补齐计数字段

    - **答案出现在排除表里 = 至少有一边确认它是错的**：合并时 verified 是取并集的，
      别的设备的 verified=True（或旧数据）会把本地降级冲掉，这里按排除表降回去
      （下一轮答完会重新转正，自愈）
    - **未验证记录的 answer 只是「上次试过的答案」**，它同时也应该留在排除表里
      （update_by_feedback 答错时就是这么写的）——这里不能删，
      删了下次 AI 又会被引导去选同一个错项，同一道题会永远答错下去
    """
    answer = record.get('answer', '')
    wrong = []
    for item in record.get('wrong', []) or []:
        if item and item not in wrong:
            wrong.append(item)

    if record.get('verified') and answer and any(_same_text(item, answer) for item in wrong):
        record['verified'] = False
        logger.warning(f'合并：答案 {answer!r} 在排除表里，verified 降级 [{record.get("id")}]')

    record['wrong'] = wrong
    record['hits'] = int(record.get('hits', 0) or 0)
    record['correct'] = int(record.get('correct', 0) or 0)
    if not answer:
        record['verified'] = False


def merge_questions(local: list, remote: list) -> tuple:
    """
    按 id 归并两份题目列表

    同 id 但选项不同的记录（旧数据里 id 只由题干决定留下的）不合并：
    已有记录改挂变体 id，另一份继续用原 id，两份都保留；否则选项组和答案
    会互相覆盖，同一条记录在两边同步时反复被改。

    :return: (合并后的题目列表, 统计 {'added', 'updated', 'conflict', 'split', 'changed'})
    """
    merged = {}
    for item in local:
        if item.get('id') and item.get('q'):
            merged[item['id']] = copy.deepcopy(item)

    stats = {'added': 0, 'updated': 0, 'conflict': 0, 'split': 0, 'changed': False}
    for item in remote:
        if not item.get('id') or not item.get('q'):
            continue
        target = merged.get(item['id'])
        if target is not None and _options_conflict(target.get('options'), item.get('options')):
            # 同 id 但选项不同：这是两道题。已有记录按「题干 + 选项」改挂变体 id，
            # 另一份继续用原 id；两边各同步一次后 id 就稳定了
            variant_id = QuestionBank.make_variant_id(target['q'], target.get('options'))
            variant = merged.pop(item['id'], None)
            if variant is not None and merged.get(variant_id) is None:
                variant['id'] = variant_id
                merged[variant_id] = variant
            elif variant is not None:
                changed, conflict = _merge_record(merged[variant_id], variant)
                if changed:
                    stats['updated'] += 1
                    stats['changed'] = True
                if conflict:
                    stats['conflict'] += 1
            merged[item['id']] = copy.deepcopy(item)
            stats['split'] += 1
            stats['changed'] = True
            continue
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

    if stats['split']:
        logger.warning(f'题库合并：{stats["split"]} 条同 id 不同选项的记录已按变体 id 拆开')
    questions = list(merged.values())
    for record in questions:
        _clean_record(record)
    # 合并结果也要过一遍自检：verified 是取并集的，别的设备会把「答案对不上选项」的串题记录
    # 又合并回来（_clean_record 还会把降级时留的排除项按「与答案相同」清掉），必须放在最后
    fixed = QuestionBank.sanitize_questions(questions)
    if any(fixed.values()):
        logger.warning(f'合并结果自检修正 {sum(fixed.values())} 处'
                       f'（降级 {fixed["demoted"]}，清理排除项 {fixed["dropped_wrong"]}）')
    return questions, stats


# ---------------------------------------------------------------- git
def _git(repo_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(('git', '-C', repo_dir) + args,
                          capture_output=True, encoding='utf-8', errors='replace')


def _git_ok(repo_dir: str, *args: str) -> bool:
    return _git(repo_dir, *args).returncode == 0


def _ensure_repo(repo_dir: str, url: str = '') -> bool:
    """
    交换仓库目录不可用时按 url 自动 clone；已存在时确保 origin 指向当前仓库

    旧目录的 origin 不是 Gitee 地址时，这里会自动改过来，
    不需要用户手动删目录。
    """
    if not repo_dir:
        return False
    url = url or DEFAULT_BANK_REPO_URL
    if os.path.isdir(os.path.join(repo_dir, '.git')):
        current = _git(repo_dir, 'remote', 'get-url', 'origin')
        if current.returncode != 0:
            _git_ok(repo_dir, 'remote', 'add', 'origin', url)
        elif current.stdout.strip() != url:
            logger.info(f'题库仓库地址切换: {current.stdout.strip()} -> {url}')
            _git_ok(repo_dir, 'remote', 'set-url', 'origin', url)
        return True
    if os.path.exists(os.path.join(repo_dir, BANK_NAME)):
        logger.warning(f'题库仓库目录没初始化 git: {repo_dir}')
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
                f'(added {stats["added"]}, updated {stats["updated"]}, '
                f'conflict {stats["conflict"]}, split {stats["split"]})')
    if stats['conflict']:
        logger.warning(f'题库有 {stats["conflict"]} 条答案冲突，请在题库网页工具里人工核对')
    if stats['changed']:
        QuestionBank.dump(bank_path, merged)
    return stats


def _canonical_questions(questions: list) -> str:
    """按 id 排序后的规范 JSON，用来判断合并结果是否真的变化"""
    return json.dumps(sorted(questions, key=lambda item: str(item.get('id') or '')),
                      ensure_ascii=False, sort_keys=True)


def _gitee_contents_url() -> str:
    return f'{GITEE_API}/repos/{GITEE_OWNER}/{GITEE_REPO}/contents/{quote(GITEE_BANK_PATH)}'


def _gitee_get_bank(token: str, timeout: float) -> tuple:
    """读取 Gitee 仓库里的题库，文件不存在时返回空题库和 None"""
    url = f'{_gitee_contents_url()}?access_token={quote(token)}&ref={quote(GITEE_BRANCH)}'
    resp = requests.get(url, timeout=timeout)
    if resp.status_code == 404:
        return {'questions': []}, None
    resp.raise_for_status()
    data = resp.json()
    content = data.get('content') or ''
    if not content:
        raise RuntimeError('题库文件为空或过大')
    bank = json.loads(base64.b64decode(content).decode('utf-8'))
    if not isinstance(bank, dict):
        bank = {'questions': bank}
    bank.setdefault('questions', [])
    return bank, data.get('sha')


def _gitee_put_bank(bank: dict, sha, token: str, device: str, timeout: float) -> None:
    payload = {
        'content': base64.b64encode(
            json.dumps(bank, ensure_ascii=False, indent=2).encode('utf-8')).decode('ascii'),
        'message': f'sync({device or "oas"}): {len(bank.get("questions", []))} questions',
        'branch': GITEE_BRANCH,
    }
    if sha:
        payload['sha'] = sha
    resp = requests.put(_gitee_contents_url(), params={'access_token': token},
                        json=payload, timeout=timeout)
    resp.raise_for_status()


def push(bank_path: str, device: str = '', retry: int = PUSH_RETRY,
         timeout: float = 30, token: str = '') -> bool:
    """
    把本机题库合并进 Gitee 仓库并提交

    - Gitee 地址和令牌写死在模块里，用户不需要配置；
    - 合并后与远端最终内容比较，完全一致时不创建空提交；
    - 本机题库为空时跳过；
    - 网络错误 / 5xx / 429 / 409 / 422 会重试，401/403 不重试。

    :return: 是否提交成功（内容无变化也算成功）
    """
    token = (token or GITEE_TOKEN).strip()
    if not token:
        logger.warning('Gitee 令牌未配置，跳过推送')
        return False

    local = read_questions(bank_path)
    if not local:
        logger.warning('本机题库为空，跳过推送')
        return False

    for attempt in range(retry):
        try:
            remote_bank, sha = _gitee_get_bank(token, timeout)
            remote = remote_bank.get('questions', [])
            merged, stats = merge_questions(remote, local)
            if _canonical_questions(remote) == _canonical_questions(merged):
                logger.attr('Question bank push', f'{len(merged)} questions（无变化）')
                return True
            bank = {
                'version': 1,
                'updated': datetime.now().strftime('%Y-%m-%d'),
                'questions': merged,
            }
            _gitee_put_bank(bank, sha, token, device, timeout)
            logger.attr('Question bank push',
                        f'{len(merged)} questions（有更新，added {stats["added"]}, '
                        f'updated {stats["updated"]}, conflict {stats["conflict"]}, '
                        f'split {stats["split"]}）')
            return True
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            detail = str(e.response.text or '')[:200] if e.response is not None else ''
            logger.warning(f'题库推送失败（第 {attempt + 1}/{retry} 次）: '
                           f'HTTP {status} {detail}')
            if status in (401, 403, 404):
                break
            time.sleep(1 + attempt)
        except requests.RequestException as e:
            logger.warning(f'题库推送失败（第 {attempt + 1}/{retry} 次）: {e}')
            time.sleep(1 + attempt)

    logger.warning('题库推送失败，本机题库不受影响，下次答题结束后会重新同步')
    return False


# ---------------------------------------------------------------- CLI
def main() -> int:
    parser = argparse.ArgumentParser(description='题库跨设备同步（git 拉取 + Gitee API 推送）')
    sub = parser.add_subparsers(dest='command', required=True)

    merge = sub.add_parser('merge', help='合并两份题库文件（按 id 归并，verified 优先）')
    merge.add_argument('local', help='本机题库 JSON')
    merge.add_argument('remote', help='远端题库 JSON')
    merge.add_argument('-o', '--output', default='', help='输出文件，不填则覆盖 local')

    pull_parser = sub.add_parser('pull', help='从交换仓库拉取题库')
    pull_parser.add_argument('--bank', default=str(Path('tasks/Quiz/bank/quiz_zh.json')),
                             help='本机题库路径')
    pull_parser.add_argument('--repo', default=DEFAULT_BANK_REPO_DIR, help='题库交换仓库目录')
    pull_parser.add_argument('--url', default=DEFAULT_BANK_REPO_URL,
                             help='仓库地址（目录不存在时自动 clone）')

    push_parser = sub.add_parser('push', help='把本机题库合并并提交到 Gitee 仓库')
    push_parser.add_argument('--bank', default=str(Path('tasks/Quiz/bank/quiz_zh.json')),
                             help='本机题库路径')
    push_parser.add_argument('--token', default='',
                             help='覆盖内置 Gitee 令牌（一般不用填）')
    push_parser.add_argument('--device', default='', help='设备名（写进提交信息）')

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
    ok = push(args.bank, device=args.device, token=args.token)
    print(f'push: {"成功" if ok else "失败"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
