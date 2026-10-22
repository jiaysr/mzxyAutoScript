# This Python file uses the following encoding: utf-8
"""
题库读写与匹配

**题目身份 = 题干 + 选项**：题干只差一两个字的题目（「中国的国鸟是什么？」/「中国的国花是什么？」）
归一化后题干相似度高达 0.87，只按题干匹配会把两道题并成一道，答案互相污染
（实测题库里「中国的国鸟是什么？」的答案被写成了「牡丹」——那是国花的答案）。
所以题干模糊命中之后还要比对选项，题干和选项都像才算同一道题。

选项对不上时一律按新题处理：宁可多问一次 AI（答案会写回已有记录，不会重复建条目），
也不能拿另一道题的答案去点。

**读和写的题目身份不同**：
- 读（find）：题干模糊匹配 + 选项相似，容忍 OCR 少字/错字
- 写（write_record / ensure_entry / upsert / update_by_feedback）：
  必须「归一化题干完全相等」，模糊命中只当提示
  （装备升星 0.95 命中到装备升级，写反馈会把升级题的答案污染掉）

主库 JSON 结构：
{
  "version": 1,
  "updated": "2026-09-17",
  "questions": [
    {
      "id": "q3f8a1c20",
      "q": "每周战车战排行榜排名为多少的玩家可以获得奖励",
      "options": ["前十名", "前五名", "前十五名"],
      "answer": "前十名",         # 正确答案文字（选项顺序可能变，所以不存字母）
      "verified": true,          # 是否被游戏反馈确认过
      "wrong": ["前五名"],        # 试过且错的选项文字，用于给模型排除
      "hits": 3,
      "correct": 1,
      "source": "llm",           # llm / manual
      "updated": "2026-09-17"
    }
  ]
}

生题库 JSONL（只追加，未命中的题）：
{"t": "2026-09-17T12:30:11", "q": "...", "options": ["...", "...", "..."], "shot": "log/quiz/xxx.png"}
"""
import io
import json
import os
import random
import re
from datetime import datetime
from hashlib import md5

from difflib import SequenceMatcher

from module.logger import logger

# 题干模糊匹配阈值
MATCH_THRESHOLD = 0.85
# 题干相近时，选项整体相似度要达到这个值才算同一道题
OPTIONS_THRESHOLD = 0.75
# 答案文字与选项匹配的阈值，比题干宽松一点
OPTION_THRESHOLD = 0.8
# 排除项与所有选项的相似度都低于这个值：这条排除项跟本题的选项没关系（串题留下的脏数据）
DROP_THRESHOLD = 0.5

# 归一化时去掉的标点
PUNCTUATION = re.compile(r'[\s，。？！、：；·—…“”‘’（）()【】\[\]"\'？！,.:;!?\-_/\\|]')


class QuestionBank:
    def __init__(self, bank_path: str, unknown_path: str = None):
        """
        :param bank_path: 主库 JSON 路径
        :param unknown_path: 生题库 JSONL 路径，None 表示不记录
        """
        self.bank_path = bank_path
        self.unknown_path = unknown_path
        self.questions = []
        self._index = {}  # 归一化题干 -> [记录]（同一题干可能有多条，选项不同）
        self._by_id = {}  # id -> 记录（题干 OCR 有出入时兜底复用，避免重复建条目）

    # ---------------------------------------------------------------- 读写
    def load(self) -> 'QuestionBank':
        if os.path.exists(self.bank_path):
            with io.open(self.bank_path, encoding='utf-8') as f:
                data = json.load(f)
            self.questions = data.get('questions', [])
        else:
            logger.warning(f'题库不存在，将新建: {self.bank_path}')
            self.questions = []
        self._build_index()
        stats = self.sanitize()
        fixed = sum(stats.values())
        logger.info(f'题库已加载 {len(self.questions)} 条'
                    + (f'（自检修正 {fixed} 处：降级 {stats["demoted"]}，清理排除项 {stats["dropped_wrong"]}）'
                       if fixed else ''))
        return self

    def _build_index(self) -> None:
        self._index = {}
        self._by_id = {}
        for item in self.questions:
            self._index.setdefault(self.normalize(item.get('q', '')), []).append(item)
            if item.get('id'):
                self._by_id.setdefault(item['id'], item)

    def save(self) -> None:
        self.dump(self.bank_path, self.questions)
        self._build_index()

    @staticmethod
    def dump(path: str, questions: list) -> None:
        """
        按主库文件格式写入题目列表（题库同步/合并工具复用，保证两边格式一致）
        """
        data = {
            'version': 1,
            'updated': datetime.now().strftime('%Y-%m-%d'),
            'questions': questions,
        }
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def normalize(text: str) -> str:
        """
        归一化：去空白、统一标点，便于模糊匹配
        """
        return PUNCTUATION.sub('', text or '').lower()

    @staticmethod
    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def make_id(question: str) -> str:
        return 'q' + md5(QuestionBank.normalize(question).encode('utf-8')).hexdigest()[:9]

    # ---------------------------------------------------------------- 选项
    @staticmethod
    def option_texts(options) -> list:
        """
        选项统一成文字列表（dict 按 A/B/C 顺序，list 按原顺序），空项丢掉
        """
        if not options:
            return []
        values = [options.get(k, '') for k in sorted(options)] if isinstance(options, dict) else list(options)
        return [str(v).strip() for v in values if v and str(v).strip()]

    @classmethod
    def option_map(cls, options) -> dict:
        """
        选项统一成 {'A': 文字, 'B': 文字, ...}（list 按 A/B/C 顺序编号）
        """
        return {chr(ord('A') + i): text for i, text in enumerate(cls.option_texts(options))}

    @classmethod
    def option_similarity(cls, a: str, b: str) -> float:
        """两个选项文字的相似度（先归一化）"""
        return cls.similarity(cls.normalize(a), cls.normalize(b))

    @classmethod
    def options_similarity(cls, a, b) -> float:
        """
        两组选项的整体相似度，范围 0~1：顺序无关，双向各自取最佳配对再平均

        选项顺序会变所以按文字配对；个别字被 OCR 认错时仍能拿高分
        （三个选项里错一个：约 0.89；错两个：约 0.78；完全不同的两组：接近 0）
        """
        ta, tb = cls.option_texts(a), cls.option_texts(b)
        if not ta or not tb:
            return 0.0

        def one_way(src: list, dst: list) -> float:
            return sum(max(cls.option_similarity(s, d) for d in dst) for s in src) / len(src)

        return (one_way(ta, tb) + one_way(tb, ta)) / 2

    @staticmethod
    def orphan_wrong(options: dict, wrong: list) -> list:
        """
        排除表里对不上任何已知选项文字的条目

        选项文字没读全（OCR 缺项）时，这些条目说明「被排除的选项正是那个没读出来的」，
        调用方据此判断能不能相信模型给回来的字母
        """
        texts = [text for text in (options or {}).values() if text]
        return [item for item in (wrong or [])
                if item and not any(QuestionBank.option_similarity(text, item) >= OPTION_THRESHOLD
                                    for text in texts)]

    # ---------------------------------------------------------------- 查询
    def find(self, question: str, options=None) -> tuple:
        """
        查找题目：题干相似 **且** 选项相同才算同一道题

        :param question: 本次 OCR 的题干
        :param options: 本次 OCR 的选项 {'A': ..., 'B': ..., 'C': ...}，不给就只按题干判断
        :return: (record, score)，没命中返回 (None, 最高分)
        """
        target = self.normalize(question)
        if not target:
            return None, 0.0
        known = bool(self.option_texts(options))

        # 精确题干：同题干的记录可能有多条（选项不同），按选项挑最贴合的一条
        candidates = self._index.get(target) or []
        if candidates:
            best = candidates[0]
            if known and len(candidates) > 1:
                best = max(candidates, key=lambda r: self.options_similarity(r.get('options'), options))
            if not known or self.options_similarity(best.get('options'), options) >= OPTIONS_THRESHOLD:
                return best, 1.0
            logger.warning(f'题干完全相同但选项不一致，按新题处理: {question[:24]}')

        # 模糊题干：题干分和选项分各占一半挑候选，但两个分都要达标才认
        best, best_score, best_q, best_opt = None, 0.0, 0.0, 0.0
        for item in self.questions:
            q_score = self.similarity(target, self.normalize(item.get('q', '')))
            opt_score = self.options_similarity(item.get('options'), options) if known else 0.0
            score = q_score if not known else (q_score + opt_score) / 2
            if score > best_score:
                best, best_score, best_q, best_opt = item, score, q_score, opt_score
        if best is None or best_q < MATCH_THRESHOLD:
            return None, best_score
        if known and best_opt < OPTIONS_THRESHOLD:
            logger.info(f'题干相近但选项不同，按新题处理: {question[:24]} '
                        f'(题干 {best_q:.2f} 选项 {best_opt:.2f})')
            return None, best_score
        return best, best_score

    def exact_record(self, question: str, options=None) -> dict | None:
        """
        按「归一化题干完全相等」定位记录（读写分家：这是**写入**用的）

        find() 是模糊匹配，适合读题库；写反馈必须落在题干完全相等的记录上，
        否则「装备升星 / 装备升级」这种相近题会把答案、排除表写串
        （实测升星题拿走了升级题的已验证答案，答错后还污染了升级题的记录）。

        题干完全相同时可能有多条（选项不同），按选项挑最贴合的一条；
        选项没读出来就取第一条。
        """
        target = self.normalize(question)
        if not target:
            return None
        candidates = self._index.get(target) or []
        if not candidates:
            return None
        if len(candidates) == 1 or not self.option_texts(options):
            return candidates[0]
        return max(candidates, key=lambda r: self.options_similarity(r.get('options'), options))

    def write_record(self, question: str, options=None, record: dict = None) -> dict | None:
        """
        解析本次反馈要写入的记录：题干不完全相等就按精确题干重新找

        调用方给的 record 可能来自 find() 的模糊命中，直接写会把两道题混在一起。
        找不到精确记录返回 None，由调用方决定是否新建（见 ensure_entry）。

        题干没读出来（空串）时直接返回传入的记录：那时 find 本来也不会命中，
        调用方给什么就写什么，保持旧行为。
        """
        target = self.normalize(question)
        if not target:
            return record
        if record is not None and self.normalize(record.get('q', '')) == target:
            return record
        return self.exact_record(question, options)

    @classmethod
    def best_option(cls, options: dict, answer_text: str) -> tuple:
        """
        选项里跟 answer_text 最像的一条
        :return: (letter, score)，选不出来返回 ('', 0.0)
        """
        target = cls.normalize(answer_text)
        if not target or not options:
            return '', 0.0
        best, best_score = '', 0.0
        for letter, text in options.items():
            score = cls.option_similarity(text, answer_text)
            if score > best_score:
                best, best_score = letter, score
        return best, best_score

    def match_option(self, options: dict, answer_text: str) -> str:
        """
        把答案文字匹配到 A/B/C 中某一行
        :param options: {'A': '前十名', 'B': '前五名', 'C': '前十五名'}
        :param answer_text: 正确答案文字
        :return: 'A' / 'B' / 'C'，匹配不到返回 ''
        """
        letter, score = self.best_option(options, answer_text)
        if letter and score >= OPTION_THRESHOLD:
            return letter
        logger.warning(f'答案 {answer_text} 匹配不到选项: {options} (最高分 {score:.2f})')
        return ''

    @classmethod
    def find_excluded(cls, answer_text: str, wrong: list) -> str:
        """
        答案文字是否命中排除表（兼容 OCR 写法差异）
        :return: 命中的排除项文字，没命中返回 ''
        """
        if not answer_text or not wrong:
            return ''
        target = cls.normalize(answer_text)
        best, best_score = '', 0.0
        for item in wrong:
            score = cls.similarity(target, cls.normalize(item))
            if score > best_score:
                best, best_score = item, score
        return best if best_score >= OPTION_THRESHOLD else ''

    def allowed_letters(self, options: dict, wrong: list = None) -> list:
        """
        选项里没有被排除表挡住的字母

        排除是硬约束：进了排除表的选项任何路径都不再点（点过的错选项不会再点第二次）
        """
        return [letter for letter, text in (options or {}).items()
                if text and not self.find_excluded(text, wrong or [])]

    def option_letter(self, options: dict, answer_text: str, wrong: list = None) -> str:
        """
        答案文字 -> 可点的字母：被排除表挡住、或匹配不上选项时返回 ''

        所有「拿现成答案去点」的路径都走这里，避免把已排除的选项再点一遍
        """
        if not options or not answer_text:
            return ''
        if self.find_excluded(answer_text, wrong or []):
            logger.warning(f'答案 {answer_text!r} 在排除表里，不使用')
            return ''
        allowed = {letter: options[letter] for letter in self.allowed_letters(options, wrong)}
        if not allowed:
            logger.warning(f'选项都被排除表挡住了，无法使用答案 {answer_text!r}')
            return ''
        return self.match_option(allowed, answer_text)

    def pick_letter(self, options: dict, wrong: list = None, mode: str = 'random',
                    record: dict = None) -> str:
        """
        兜底挑一个可点的字母：永远不挑排除表里的选项
        :param mode: 'first' 固定第一个可用选项 / 'random' 随机 / 'skip' 不作答
        :param record: 传了记录时，若已知选项全被排除（自相矛盾）就清空排除表重来
        :return: 字母，没得点返回 ''
        """
        if mode == 'skip':
            return ''
        options = options or {}
        if not self.option_texts(options) and not wrong:
            # 选项文字完全没读出来、也没有排除表：闭眼选一个（抢答总比干等着超时强）
            return 'A' if mode == 'first' else random.choice(('A', 'B', 'C'))
        allowed = self.allowed_letters(options, wrong or [])
        if not allowed and record is not None and record.get('wrong') and len(self.option_texts(options)) >= 2:
            # 三个选项都在排除表里说明数据自相矛盾，留着就只能点已排除的选项
            logger.warning('所有选项都在排除表里，清空该题的排除记录重新判断')
            self.clear_wrong(record)
            allowed = self.allowed_letters(options, [])
        if not allowed:
            return ''
        return allowed[0] if mode == 'first' else random.choice(allowed)

    # ---------------------------------------------------------------- 写入
    def ensure_entry(self, question: str, options=None) -> dict:
        """
        确保题目在库里（答案可以暂时为空）

        用途：在调用 AI 之前先把题目落库，这样即使 API 超时/报错也不会丢题，
        留下一道 answer 为空的记录，下次遇到会重新作答，也可以人工补。

        定位只看「归一化题干完全相等」的记录（见 write_record）：模糊命中是另一道题时
        （如升星 vs 升级）要新建条目，不能把反馈写到别人的记录上。

        题干 OCR 有出入导致没命中时，再按 id 兜一次：宁可复用已有记录，
        也不给同一道题干建出两条（id 只由题干决定，两条同 id 的记录在题库同步时会互相覆盖）
        """
        record = self.write_record(question, options)
        option_list = self.option_texts(options)
        if record is None and question:
            record = self._by_id.get(self.make_id(question))
        if record is not None:
            if option_list:
                record['options'] = option_list
            return record

        record = {
            'id': self.make_id(question),
            'q': question,
            'options': option_list,
            'answer': '',
            'verified': False,
            'wrong': [],
            'hits': 0,
            'correct': 0,
            'source': 'pending',
            'updated': datetime.now().strftime('%Y-%m-%d'),
        }
        self.questions.append(record)
        logger.info(f'题库先记录题目（待作答）: {question[:20]}...')
        self.save()
        return record

    def upsert(self, question: str, options=None, answer_text: str = '',
               source: str = 'llm', record: dict = None) -> dict:
        """
        写入或更新一条题目的题目/选项/答案（不碰 verified，由 update_by_feedback 管）
        :param record: 已经定位到的记录（答题流程里由 find 给出），不传就按题干+选项再找一次；
                       题干不完全相等的模糊命中会被换成精确记录（见 write_record）
        """
        record = self.write_record(question, options, record)
        today = datetime.now().strftime('%Y-%m-%d')
        option_list = self.option_texts(options)
        if record is None:
            record = {
                'id': self.make_id(question),
                'q': question,
                'options': option_list,
                'answer': answer_text,
                'verified': False,
                'wrong': [],
                'hits': 0,
                'correct': 0,
                'source': source,
                'updated': today,
            }
            self.questions.append(record)
            logger.info(f'题库新增: {question[:20]}... -> {answer_text}')
        else:
            if option_list:
                record['options'] = option_list
            if answer_text:
                record['answer'] = answer_text
            record['source'] = source
            record['updated'] = today
            logger.info(f'题库更新: {question[:20]}... -> {answer_text}')
        self.save()
        return record

    def update_by_feedback(self, question: str, answer_text: str, feedback: str,
                           record: dict = None, options=None) -> None:
        """
        用游戏反馈更新题库（不管答对答错都会走到这里，题库命中也一样）

        :param answer_text: 本次提交的答案文字
        :param feedback: 'correct' / 'wrong' / None
        :param record: 已经定位到的记录（答题流程里由 find 给出）；
                       题干不完全相等时改用精确记录，没有就新建（见 write_record）
        :param options: 本次 OCR 的选项，用于顺手刷新记录里的选项
        """
        record = self.write_record(question, options, record)
        if record is None and question:
            # 模糊命中到别的题（如升星命中升级）时不能写别人，给本题新建一条
            record = self.ensure_entry(question, options)
        if record is None:
            return
        record['hits'] = record.get('hits', 0) + 1
        option_list = self.option_texts(options)
        if option_list:
            record['options'] = option_list

        if feedback == 'correct':
            if answer_text:
                record['answer'] = answer_text
                # 该答案已被游戏确认，从排除列表里移除（选项顺序会变，留着会误导 AI）；
                # OCR 写法有出入时用相似度判断，否则「已确认的答案」会一直留在排除表里
                record['wrong'] = [w for w in record.get('wrong', [])
                                   if self.option_similarity(w, answer_text) < OPTION_THRESHOLD]
            record['verified'] = True
            # 答对次数：多设备同步合并时作为答案可信度的依据
            record['correct'] = int(record.get('correct', 0) or 0) + 1
            logger.info(f'题库校验通过: {question[:20]}... -> {record["answer"]}')
        elif feedback == 'wrong':
            record['verified'] = False
            if answer_text:
                record['answer'] = answer_text
            wrong = record.setdefault('wrong', [])
            # 同样的错误选项只记一条（OCR 写法差异不算新的）
            if answer_text and not self.find_excluded(answer_text, wrong):
                wrong.append(answer_text)
            logger.warning(f'题库校验失败，降级为未验证: {question[:20]}... - {answer_text}')
        else:
            logger.warning(f'没读到反馈，题库不做升降级: {question[:20]}...')
        self.save()

    def add_wrong(self, question: str, wrong_text: str, options=None, record: dict = None) -> None:
        """
        记录一个试过且错误的选项（排除法用）
        """
        record = self.write_record(question, options, record)
        if record is None:
            return
        wrong = record.setdefault('wrong', [])
        if wrong_text and not self.find_excluded(wrong_text, wrong):
            wrong.append(wrong_text)
            logger.info(f'记录错误选项: {question[:20]}...  {wrong_text}')
            self.save()

    def clear_wrong(self, record: dict) -> None:
        """
        清空一道题的排除表（选项全被排除 = 数据自相矛盾，继续留着只能点错选项）
        """
        if record and record.get('wrong'):
            record['wrong'] = []
            self.save()

    # ---------------------------------------------------------------- 自检
    def sanitize(self) -> dict:
        """
        载入后自检，修正历史上被「题干相近就当成同一题」污染的记录
        （规则见 sanitize_questions）
        """
        return self.sanitize_questions(self.questions)

    @classmethod
    def sanitize_questions(cls, questions: list) -> dict:
        """
        自检一份题目列表（原地修改），载入题库和题库同步合并后都跑一遍：

        - 答案跟本题选项对不上的（串题写进来的答案，如「中国的国鸟」存了「牡丹」）
          降级为未验证并把该答案记进排除表：下次重新作答。排除表这一手是必须的——
          题库同步合并时 verified 取并集，别的设备把它合并回来后就只剩排除表能挡
        - 排除项跟本题所有选项都不沾边的（串题留下的），清掉

        :return: {'demoted': 降级条数, 'dropped_wrong': 清理的排除项条数}
        """
        stats = {'demoted': 0, 'dropped_wrong': 0}
        for record in questions:
            options = cls.option_map(record.get('options'))
            answer = record.get('answer') or ''
            if answer and options:
                _, score = cls.best_option(options, answer)
                if score < OPTION_THRESHOLD:
                    record['verified'] = False
                    # 这个答案的「答对次数」是别的题的，不能拿它去跟别的设备比可信度
                    record['correct'] = 0
                    wrong = record.setdefault('wrong', [])
                    if not cls.find_excluded(answer, wrong):
                        wrong.append(answer)
                    stats['demoted'] += 1
                    logger.warning(f'题库自检：答案 {answer!r} 对不上选项 {list(options.values())}，'
                                   f'降级为未验证并记入排除表 [{record.get("id")}] {record.get("q", "")[:20]}...')
            wrong = record.get('wrong') or []
            if wrong and options:
                # 跟选项沾边的排除项才有意义；跟答案相同的那条是上面降级时特意留的（防同步把
                # verified 合并回来），不能当噪声清掉
                keep = [w for w in wrong
                        if cls.best_option(options, w)[1] >= DROP_THRESHOLD
                        or cls.option_similarity(w, answer) >= OPTION_THRESHOLD]
                if len(keep) != len(wrong):
                    stats['dropped_wrong'] += len(wrong) - len(keep)
                    logger.warning(f'题库自检：排除项 {sorted(set(wrong) - set(keep))} 跟本题选项无关，已清理'
                                   f' [{record.get("id")}] {record.get("q", "")[:20]}...')
                    record['wrong'] = keep
        return stats

    # ---------------------------------------------------------------- 生题库
    def append_unknown(self, question: str, options: dict, image_path: str = '') -> None:
        """
        未命中时追加到生题库，方便事后人工补
        """
        if not self.unknown_path:
            return
        os.makedirs(os.path.dirname(self.unknown_path), exist_ok=True)
        line = {
            't': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'q': question,
            'options': [options.get(k, '') for k in ('A', 'B', 'C')] if isinstance(options, dict)
            else list(options or []),
            'shot': image_path,
        }
        with io.open(self.unknown_path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(line, ensure_ascii=False) + '\n')
