# This Python file uses the following encoding: utf-8
"""
题库读写与匹配

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
import re
from datetime import datetime
from hashlib import md5

from difflib import SequenceMatcher

from module.logger import logger

# 题干模糊匹配阈值
MATCH_THRESHOLD = 0.85
# 答案文字与选项匹配的阈值，比题干宽松一点
OPTION_THRESHOLD = 0.8

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
        self._index = {}  # 归一化题干 -> 记录

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
        logger.info(f'题库已加载 {len(self.questions)} 条')
        return self

    def _build_index(self) -> None:
        self._index = {}
        for item in self.questions:
            self._index[self.normalize(item['q'])] = item

    def save(self) -> None:
        data = {
            'version': 1,
            'updated': datetime.now().strftime('%Y-%m-%d'),
            'questions': self.questions,
        }
        os.makedirs(os.path.dirname(self.bank_path), exist_ok=True)
        with io.open(self.bank_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        self._build_index()

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

    # ---------------------------------------------------------------- 查询
    def find(self, question: str) -> tuple:
        """
        模糊查找题干
        :return: (record, score)，没命中返回 (None, 最高分)
        """
        target = self.normalize(question)
        if not target:
            return None, 0.0

        # 先精确命中
        if target in self._index:
            return self._index[target], 1.0

        best, best_score = None, 0.0
        for item in self.questions:
            score = self.similarity(target, self.normalize(item['q']))
            if score > best_score:
                best, best_score = item, score
        if best_score >= MATCH_THRESHOLD:
            return best, best_score
        return None, best_score

    def match_option(self, options: dict, answer_text: str) -> str:
        """
        把答案文字匹配到 A/B/C 中某一行
        :param options: {'A': '前十名', 'B': '前五名', 'C': '前十五名'}
        :param answer_text: 正确答案文字
        :return: 'A' / 'B' / 'C'，匹配不到返回 ''
        """
        target = self.normalize(answer_text)
        if not target:
            return ''
        best, best_score = '', 0.0
        for letter, text in options.items():
            score = self.similarity(target, self.normalize(text))
            if score > best_score:
                best, best_score = letter, score
        if best_score >= OPTION_THRESHOLD:
            return best
        logger.warning(f'答案 {answer_text} 匹配不到选项: {options} (最高分 {best_score:.2f})')
        return ''

    # ---------------------------------------------------------------- 写入
    def upsert(self, question: str, options: list, answer_text: str,
               verified: bool, source: str = 'llm') -> dict:
        """
        写入或更新一条题目（不管对错都写，用 verified 区分可信度）
        :return: 写入后的记录
        """
        record, score = self.find(question)
        today = datetime.now().strftime('%Y-%m-%d')
        if record is None:
            record = {
                'id': self.make_id(question),
                'q': question,
                'options': list(options),
                'answer': answer_text,
                'verified': bool(verified),
                'wrong': [],
                'hits': 0,
                'source': source,
                'updated': today,
            }
            self.questions.append(record)
            logger.info(f'题库新增: {question[:20]}... -> {answer_text} (verified={verified})')
        else:
            record['options'] = list(options) or record.get('options', [])
            record['answer'] = answer_text
            record['source'] = source
            record['updated'] = today
            if verified:
                record['verified'] = True
                # 被游戏确认过的答案：wrong 里是"这些文字是错误答案"，与选项顺序无关，保留
                logger.info(f'题库更新为已验证: {question[:20]}... -> {answer_text}')
            else:
                record.setdefault('verified', False)
                logger.info(f'题库更新(未验证): {question[:20]}... -> {answer_text}')
        record['hits'] = record.get('hits', 0) + 1
        self.save()
        return record

    def add_wrong(self, question: str, wrong_text: str) -> None:
        """
        记录一个试过且错误的选项（排除法用）
        """
        record, _ = self.find(question)
        if record is None:
            return
        wrong = record.setdefault('wrong', [])
        if wrong_text and wrong_text not in wrong:
            wrong.append(wrong_text)
            logger.info(f'记录错误选项: {question[:20]}...  {wrong_text}')
            self.save()

    def verify(self, question: str) -> None:
        """
        游戏反馈答对时调用，把该题标记为已验证
        """
        record, _ = self.find(question)
        if record is None:
            return
        if not record.get('verified'):
            record['verified'] = True
            record['wrong'] = []
            logger.info(f'题库确认: {question[:20]}... -> {record["answer"]}')
            self.save()

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
            'options': [options.get(k, '') for k in ('A', 'B', 'C')],
            'shot': image_path,
        }
        with io.open(self.unknown_path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(line, ensure_ascii=False) + '\n')
