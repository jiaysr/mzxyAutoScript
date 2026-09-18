# This Python file uses the following encoding: utf-8
"""
每日答题

流程：
    进入答题入口 -> 参加 -> 循环作答 total_questions 题 -> 结算

每题：
    1. 本地 OCR 题干 + 三个选项（免费）
    2. 题库命中且已验证 -> 直接用题库答案，不调 API
    3. 未命中 / 未验证 / OCR 失败 -> 调 DeepSeek 看截图作答
    4. 点击选项 -> 读游戏反馈（正确/错误）
    5. 回写题库：答对标记 verified，答错记录错误选项供下次排除

────────────────────────────────────────────────────────────────────────
需要手动添加的素材（用 IDE 标注工具在 tasks/Quiz/res 里裁剪，生成 assets.py）：

  必须：
    I_QUIZ_ENTRY        答题入口按钮
    C_QUIZ_START        「参加答题 / 开始」按钮
    I_QUIZ_DIALOG       答题弹窗特征（用来判断弹窗在不在）
    O_QUIZ_PROGRESS     OCR 区域，读「第3/20题」
    C_QUIZ_OPTION_A     选项 A 那一行的点击区域
    C_QUIZ_OPTION_B     选项 B 那一行的点击区域
    C_QUIZ_OPTION_C     选项 C 那一行的点击区域
    I_QUIZ_CORRECT      答对反馈图标
    I_QUIZ_WRONG        答错反馈图标
    I_QUIZ_FINISH       结算页 / 今日已完成 的判定特征

  可选（配了就能走"题库优先、不花 API 钱"的路子）：
    O_QUIZ_QUESTION     OCR 题干区域
    O_QUIZ_OPTION_A     OCR 选项 A 的文字
    O_QUIZ_OPTION_B     OCR 选项 B 的文字
    O_QUIZ_OPTION_C     OCR 选项 C 的文字

  可选（导航用，看答题入口在哪个界面）：
    C_QUIZ_ENTRY_CLICK  入口点击区域（默认点 I_QUIZ_ENTRY 匹配到的位置）
────────────────────────────────────────────────────────────────────────
"""
import os
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

import cv2

from module.base.timer import Timer
from module.base.utils import crop
from module.exception import GameStuckError, RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Quiz.assets import QuizAssets
from tasks.Quiz.deepseek_client import DeepSeekClient
from tasks.Quiz.question_bank import QuestionBank
from tasks.base_task import BaseTask

TASK_DIR = Path(__file__).resolve().parent
BANK_FILE = str(TASK_DIR / 'bank' / 'quiz_zh.json')
UNKNOWN_FILE = str(TASK_DIR / 'bank' / 'quiz_unknown.jsonl')
SHOT_DIR = './log/quiz'

# 交给 AI 识别的弹窗区域 (x, y, w, h)：左侧题干 + 三个选项，不要带右边答题榜
# 依据 1280x720 截图，x 0~600 / y 55~400
DIALOG_AREA = (10, 60, 590, 340)

# 没素材就跑不了
REQUIRED_ASSETS = [
    'I_QUIZ_ENTRY',
    'C_QUIZ_START',
    'I_QUIZ_DIALOG',
    'O_QUIZ_PROGRESS',
    'C_QUIZ_OPTION_A',
    'C_QUIZ_OPTION_B',
    'C_QUIZ_OPTION_C',
    'I_QUIZ_CORRECT',
    'I_QUIZ_WRONG',
    'I_QUIZ_FINISH',
]

# 可选素材，有就走本地 OCR 省钱
OPTIONAL_ASSETS = [
    'O_QUIZ_QUESTION',
    'O_QUIZ_OPTION_A',
    'O_QUIZ_OPTION_B',
    'O_QUIZ_OPTION_C',
]

OPTION_CLICKS = {
    'A': 'C_QUIZ_OPTION_A',
    'B': 'C_QUIZ_OPTION_B',
    'C': 'C_QUIZ_OPTION_C',
}
OPTION_OCR = {
    'A': 'O_QUIZ_OPTION_A',
    'B': 'O_QUIZ_OPTION_B',
    'C': 'O_QUIZ_OPTION_C',
}
# 选项文字前的 "A ：" 之类前缀
OPTION_PREFIX = re.compile(r'^\s*[A-Ca-c]\s*[:：.、]?\s*')


class ScriptTask(BaseTask, QuizAssets):
    def run(self) -> None:
        self.check_assets()

        self.bank = QuestionBank(BANK_FILE, UNKNOWN_FILE).load()
        self.client = DeepSeekClient(
            api_key=self.quiz_config.api_key or os.environ.get('DEEPSEEK_API_KEY', ''),
            model=self.quiz_config.model,
            timeout=self.quiz_config.api_timeout,
            use_thinking=self.quiz_config.use_thinking,
        )
        self.device.stuck_record_add('QUIZ')

        self.enter_quiz()
        if not self.start_quiz():
            logger.info('今日答题已完成，跳过')
            self.schedule_next_day()
            raise TaskEnd('Quiz')

        self.answer_loop()

        logger.info('本局答题结束')
        self.schedule_next_day()
        raise TaskEnd('Quiz')

    # ---------------------------------------------------------------- 基础
    @property
    def quiz_config(self):
        return self.config.quiz.quiz_config

    def check_assets(self) -> None:
        """
        素材没齐时给出明确提示，而不是 AttributeError
        """
        missing = [name for name in REQUIRED_ASSETS if not hasattr(self, name)]
        if missing:
            logger.critical('答题素材缺失，请在 tasks/Quiz/res 里补齐：')
            for name in missing:
                logger.critical(f'  {name}')
            raise RequestHumanTakeover
        lacking = [name for name in OPTIONAL_ASSETS if not hasattr(self, name)]
        if lacking:
            logger.warning(f'可选素材缺失，将只能走 AI 识别：{", ".join(lacking)}')

    def schedule_next_day(self) -> None:
        target = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        self.set_next_run(task='Quiz', target=target, success=True, finish=True)
        logger.info(f'下次答题: {target.strftime("%Y-%m-%d %H:%M")}')

    # ---------------------------------------------------------------- 导航
    def enter_quiz(self, timeout: int = 30) -> bool:
        """
        打开答题界面（入口位置等素材补齐后确认）
        """
        logger.hr('Enter quiz')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_DIALOG) or self.appear(self.I_QUIZ_FINISH):
                logger.info('Quiz page appear')
                return True
            if timer.reached():
                raise GameStuckError('答题界面没有出现')
            if self.appear_then_click(self.I_QUIZ_ENTRY, interval=1):
                logger.info('Click quiz entry')
                continue

    def start_quiz(self, timeout: int = 10) -> bool:
        """
        点「参加答题」
        :return: True 开始成功；False 今日已完成
        """
        logger.hr('Start quiz')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_FINISH):
                return False
            if self.appear(self.I_QUIZ_DIALOG):
                logger.info('Quiz dialog appear')
                return True
            if timer.reached():
                raise GameStuckError('答题弹窗没有出现')
            if self.appear_then_click(self.C_QUIZ_START, interval=1):
                logger.info('Click start quiz')
                continue
            self.device.sleep(0.3)

    # ---------------------------------------------------------------- 答题循环
    def answer_loop(self) -> None:
        logger.hr('Answer loop')
        total = self.quiz_config.total_questions
        done = 0
        last_index = 0

        while done < total:
            if not self.wait_next_question(last_index):
                logger.info('答题结束（弹窗消失）')
                return
            index = self.current_index()
            last_index = index or (last_index + 1)
            done += 1
            logger.hr(f'Question {done}/{total}', level=2)
            self.answer_one()

        logger.info(f'已作答 {done} 题')
        self.wait_until_finish()

    def answer_one(self) -> None:
        """
        答一题：题库优先 -> AI 兜底 -> 点击 -> 反馈 -> 回写
        """
        self.screenshot()
        question, options = self.ocr_locally()
        record, score = self.bank.find(question) if question else (None, 0.0)

        # 题库可信就直接用，不调 API
        if record and record.get('verified') and options:
            letter = self.bank.match_option(options, record['answer'])
            if letter:
                logger.info(f'题库命中({score:.2f}): {record["answer"]} -> {letter}')
                self.click_option(letter)
                feedback = self.read_feedback()
                if feedback == 'correct':
                    self.bank.verify(question)
                elif feedback == 'wrong':
                    logger.warning('题库答案被判错，标记为未验证')
                    record['verified'] = False
                    self.bank.add_wrong(question, record['answer'])
                return

        # 否则问 AI
        ai = self.ask_ai(wrong_options=record.get('wrong') if record else None)
        if ai is None:
            # API 挂了，退而求其次用题库里的答案
            if record and options:
                letter = self.bank.match_option(options, record['answer'])
                if letter:
                    logger.warning('API 不可用，使用题库答案')
                    self.click_option(letter)
                    return
            self.fallback_pick()
            return

        question = ai.get('question') or question
        options = ai.get('options') or options
        answer_text = ai.get('answer_text') or ''
        letter = ai.get('answer', '').strip().upper()

        letter = self.bank.match_option(options, answer_text) or letter
        if letter not in OPTION_CLICKS:
            logger.warning(f'无法确定选项: answer_text={answer_text!r} options={options}')
            self.bank.append_unknown(question, options)
            self.fallback_pick()
            return

        self.click_option(letter)
        feedback = self.read_feedback()

        if question and self.quiz_config.learn_from_llm:
            # 不管对错都入库，用 verified 区分可信度
            self.bank.upsert(
                question,
                [options.get(k, '') for k in ('A', 'B', 'C')],
                answer_text or options.get(letter, ''),
                verified=(feedback == 'correct'),
            )
            if feedback == 'wrong':
                self.bank.add_wrong(question, answer_text or options.get(letter, ''))
        if feedback is None:
            self.bank.append_unknown(question, options)

    def click_option(self, letter: str) -> None:
        logger.info(f'Click option {letter}')
        self.click(getattr(self, OPTION_CLICKS[letter]))

    def read_feedback(self, timeout: int = 3) -> str:
        """
        :return: 'correct' / 'wrong' / None（没读到）
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_CORRECT):
                logger.info('答对了')
                return 'correct'
            if self.appear(self.I_QUIZ_WRONG):
                logger.info('答错了')
                return 'wrong'
            if timer.reached():
                logger.warning('没有读到答题反馈')
                return None
            self.device.sleep(0.3)

    def fallback_pick(self) -> None:
        mode = self.quiz_config.unknown_fallback
        if mode == 'skip':
            logger.warning('兜底策略：不作答')
            return
        letter = 'A' if mode == 'first' else random.choice(['A', 'B', 'C'])
        logger.warning(f'兜底策略：{mode} -> {letter}')
        self.click_option(letter)

    # ---------------------------------------------------------------- OCR / AI
    def ocr_locally(self) -> tuple:
        """
        本地 OCR 题干和三个选项（可选素材，缺了就返回空，走 AI）
        :return: (question, {'A': text, 'B': text, 'C': text})
        """
        if not hasattr(self, 'O_QUIZ_QUESTION'):
            return '', {}

        def read(rule):
            results = rule.detect_and_ocr(self.device.image, logDisplay=False)
            return ''.join(r.ocr_text for r in results).strip()

        question = read(self.O_QUIZ_QUESTION)
        options = {}
        for letter, name in OPTION_OCR.items():
            if not hasattr(self, name):
                continue
            text = OPTION_PREFIX.sub('', read(getattr(self, name)))
            if text:
                options[letter] = text
        if question:
            logger.attr('Question(OCR)', question)
        return question, options

    def ask_ai(self, wrong_options: list = None) -> dict:
        """
        截取弹窗区域交给 DeepSeek
        """
        image = crop(self.device.image, DIALOG_AREA)
        if self.quiz_config.save_unknown_screenshot:
            os.makedirs(SHOT_DIR, exist_ok=True)
            path = f'{SHOT_DIR}/{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            cv2.imwrite(path, image)
        return self.client.answer_quiz(image, wrong_options=wrong_options)

    # ---------------------------------------------------------------- 题号/进度
    def current_index(self) -> int:
        """
        读「第 X/20 题」里的 X，读不到返回 0
        """
        results = self.O_QUIZ_PROGRESS.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(r.ocr_text for r in results)
        match = re.search(r'第?\s*(\d+)\s*[/／]', text)
        return int(match.group(1)) if match else 0

    def wait_next_question(self, last_index: int, timeout: int = 30) -> bool:
        """
        等下一题出现（题号变化）
        :return: False 表示答题结束
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_FINISH):
                return False
            in_quiz = (self.appear(self.I_QUIZ_DIALOG)
                       or self.appear(self.I_QUIZ_CORRECT)
                       or self.appear(self.I_QUIZ_WRONG))
            if in_quiz:
                index = self.current_index()
                if index == 0 or index != last_index:
                    return True
            if timer.reached():
                return False
            self.device.sleep(0.3)

    def wait_until_finish(self, timeout: int = 60) -> bool:
        logger.hr('Wait until finish')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_FINISH):
                logger.info('Quiz finish')
                return True
            if not self.appear(self.I_QUIZ_DIALOG):
                logger.info('Quiz dialog disappear')
                return True
            if timer.reached():
                logger.warning('等待结算超时')
                return False
            self.device.sleep(0.5)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
