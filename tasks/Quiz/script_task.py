# This Python file uses the following encoding: utf-8
"""
每日答题

一局 20 题、每题 20 秒；点完选项会弹出「回答正确 / 回答错误」贴纸（约 5~7 秒），
贴纸消失后才出下一题；20 题结束出现「答题结束」图。

每题流程：
    1. 本地 OCR 题干 + 三个选项（免费）
    2. 题库命中且已验证 -> 直接用题库答案，不调 API
    3. 否则交给 DeepSeek（deepseek-flash）看图作答，返回结构化 JSON
    4. 点击对应选项 -> 等贴纸 -> 判定正确/错误
    5. 回写题库：答对转正、答错记录错误选项供下次排除，不管对错都入库

素材（tasks/Quiz/res，已由标注工具生成）：
    I_QUIZ_DIALOG / I_FEEDBACK_CORRECT / I_FEEDBACK_WRONG / I_QUIZ_FINISH / I_CLOSE
    C_OPTION_A / C_OPTION_B / C_OPTION_C
    O_PROGRESS / O_QUESTION / O_OPTION_A / O_OPTION_B / O_OPTION_C
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

# 交给 AI 的弹窗区域 (x, y, w, h)：题号 + 题干 + 三个选项，不含右侧答题榜
DIALOG_AREA = (60, 120, 790, 390)

REQUIRED_ASSETS = [
    'I_QUIZ_DIALOG',
    'I_FEEDBACK_CORRECT',
    'I_FEEDBACK_WRONG',
    'I_QUIZ_FINISH',
    'C_OPTION_A',
    'C_OPTION_B',
    'C_OPTION_C',
    'O_PROGRESS',
]

OPTION_CLICKS = {'A': 'C_OPTION_A', 'B': 'C_OPTION_B', 'C': 'C_OPTION_C'}
OPTION_OCR = {'A': 'O_OPTION_A', 'B': 'O_OPTION_B', 'C': 'O_OPTION_C'}
OPTION_PREFIX = re.compile(r'^\s*[A-Ca-c]\s*[:：.、]?\s*')
PROGRESS_RE = re.compile(r'第?\s*(\d+)\s*[/／]')


class ScriptTask(BaseTask, QuizAssets):
    # 答题界面刚出现时题目还在刷新，等页面稳定再开始读题
    page_ready_wait = 2

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

        # TODO 进入答题界面的导航还没做，目前需要答题界面已经打开
        self.wait_quiz_page()

        self.answer_loop()
        self.finish_quiz()

        self.schedule_next_day()
        raise TaskEnd('Quiz')

    # ---------------------------------------------------------------- 基础
    @property
    def quiz_config(self):
        return self.config.quiz.quiz_config

    def check_assets(self) -> None:
        missing = [name for name in REQUIRED_ASSETS if not hasattr(self, name)]
        if missing:
            logger.critical('答题素材缺失，请在 tasks/Quiz/res 里补齐：')
            for name in missing:
                logger.critical(f'  {name}')
            raise RequestHumanTakeover
        lacking = [name for name in OPTION_OCR.values() if not hasattr(self, name)]
        if not hasattr(self, 'O_QUESTION') or lacking:
            logger.warning(f'题干/选项 OCR 素材缺失，只能走 AI 识别：{lacking}')

    def schedule_next_day(self) -> None:
        target = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        self.set_next_run(task='Quiz', target=target, success=True, finish=True)
        logger.info(f'下次答题: {target.strftime("%Y-%m-%d %H:%M")}')

    # ---------------------------------------------------------------- 页面
    def wait_quiz_page(self, timeout: int = 30) -> bool:
        """
        等待答题界面出现（进入逻辑待补，目前假定界面已打开）
        """
        logger.hr('Wait quiz page')
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_DIALOG) or self.appear(self.I_QUIZ_FINISH):
                logger.info('Quiz page appear')
                # 页面刚出现时题目还没刷新出来，等稳定后再读题干
                logger.info(f'Wait {self.page_ready_wait}s for the question')
                self.device.sleep(self.page_ready_wait)
                return True
            if timer.reached():
                raise GameStuckError('答题界面没有出现（进入逻辑还未实现）')
            self.device.sleep(0.5)

    def finish_quiz(self) -> None:
        """
        结束：关掉答题界面
        """
        logger.hr('Finish quiz')
        self.screenshot()
        if self.appear(self.I_QUIZ_FINISH):
            logger.info('答题结束图片出现')
        if hasattr(self, 'I_CLOSE') and self.appear_then_click(self.I_CLOSE, interval=1):
            logger.info('Close quiz dialog')
            self.device.sleep(1)

    # ---------------------------------------------------------------- 答题循环
    def answer_loop(self) -> None:
        logger.hr('Answer loop')
        total = self.quiz_config.total_questions
        answered = 0

        while answered < total:
            if not self.wait_question_ready(timeout=30):
                logger.warning('等不到下一题，结束答题')
                break
            answered += 1
            logger.hr(f'第 {answered}/{total} 题', level=2)
            self.answer_one()
            self.wait_feedback_done(timeout=15)

        logger.info(f'共作答 {answered} 题')

    def wait_question_ready(self, timeout: int = 30) -> bool:
        """
        等待可作答状态：在答题界面、且没有贴纸挡着
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_FINISH):
                return False
            if self.appear(self.I_FEEDBACK_CORRECT) or self.appear(self.I_FEEDBACK_WRONG):
                # 上一题的贴纸还在，等它消失
                if timer.reached():
                    return False
                self.device.sleep(0.3)
                continue
            if self.appear(self.I_QUIZ_DIALOG):
                return True
            if timer.reached():
                return False
            self.device.sleep(0.3)

    def answer_one(self) -> None:
        """
        答一题：题库命中直接用（答完仍校验）-> 否则先落库再问 AI -> 点击 -> 反馈 -> 回写
        """
        self.screenshot()
        question, options = self.ocr_locally()
        record, score = self.bank.find(question) if question else (None, 0.0)

        # 1) 题库命中且已验证 -> 直接用，但同样要读反馈校验题库
        if record and record.get('verified') and record.get('answer') and options:
            letter = self.bank.match_option(options, record['answer'])
            if letter:
                logger.info(f'题库命中({score:.2f}): {record["answer"]} -> {letter}')
                self.click_option(letter)
                feedback = self.read_feedback()
                # 命中也走校验：答对保持/转正，答错降级并记录错误选项
                self.bank.update_by_feedback(question, record['answer'], feedback)
                return

        # 2) 未验证但已有答案：如果配置为不重问 AI，就直接用（答完仍校验）
        if (record and record.get('answer') and options
                and not self.quiz_config.ask_again_if_unverified):
            letter = self.bank.match_option(options, record['answer'])
            if letter:
                logger.info(f'题库命中(未验证): {record["answer"]} -> {letter}')
                self.click_option(letter)
                feedback = self.read_feedback()
                self.bank.update_by_feedback(question, record['answer'], feedback)
                return

        # 3) 先把题目落库（API 失败也不丢），再问 AI
        if question and self.quiz_config.learn_from_llm:
            self.bank.ensure_entry(question, options)

        ai = self.ask_ai(question=question, options=options,
                         wrong_options=record.get('wrong') if record else None)
        if ai is None:
            logger.warning('AI 不可用（题目已落库，下次会重试）')
            if record and options and record.get('answer'):
                letter = self.bank.match_option(options, record['answer'])
                if letter:
                    logger.warning('退回题库已有答案')
                    self.click_option(letter)
                    return
            self.bank.append_unknown(question, options)
            self.fallback_pick()
            return

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
        self.learn(question, options, answer_text or options.get(letter, ''), feedback)

    def click_option(self, letter: str) -> None:
        """
        点击选项：答题是连续点 A/B/C，先清掉连点记录，避免误触框架的
        GameTooManyClickError（同一局里 A、C 各点 6 次就会触发）
        """
        logger.info(f'Click option {letter}')
        self.device.click_record_clear()
        self.click(getattr(self, OPTION_CLICKS[letter]))

    def read_feedback(self, timeout: int = 10) -> str:
        """
        等贴纸出现并判定
        :return: 'correct' / 'wrong' / None
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_FEEDBACK_CORRECT):
                logger.info('回答正确')
                return 'correct'
            if self.appear(self.I_FEEDBACK_WRONG):
                logger.info('回答错误')
                return 'wrong'
            if timer.reached():
                logger.warning('没有读到答题反馈')
                return None
            self.device.sleep(0.3)

    def wait_feedback_done(self, timeout: int = 15) -> bool:
        """
        等贴纸消失（5~7 秒），贴纸消失后才会出下一题
        """
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_QUIZ_FINISH):
                return True
            if not self.appear(self.I_FEEDBACK_CORRECT) and not self.appear(self.I_FEEDBACK_WRONG):
                return True
            if timer.reached():
                return False
            self.device.sleep(0.3)

    def fallback_pick(self) -> None:
        mode = self.quiz_config.unknown_fallback
        if mode == 'skip':
            logger.warning('兜底策略：不作答')
            return
        letter = 'A' if mode == 'first' else random.choice(['A', 'B', 'C'])
        logger.warning(f'兜底策略：{mode} -> {letter}')
        self.click_option(letter)

    # ---------------------------------------------------------------- 学习
    def learn(self, question: str, options: dict, answer_text: str,
              feedback: str) -> None:
        """
        用 AI 答案 + 游戏反馈回写题库（不管对错都写）
        """
        if not question:
            return
        if self.quiz_config.learn_from_llm:
            self.bank.upsert(
                question,
                [options.get(k, '') for k in ('A', 'B', 'C')],
                answer_text,
            )
        self.bank.update_by_feedback(question, answer_text, feedback)
        if feedback is None:
            self.bank.append_unknown(question, options)

    # ---------------------------------------------------------------- OCR / AI
    def ocr_locally(self) -> tuple:
        """
        本地 OCR 题干和三个选项，缺素材就返回空走 AI
        """
        def read(rule):
            results = rule.detect_and_ocr(self.device.image, logDisplay=False)
            return ''.join(r.ocr_text for r in results).strip()

        question = read(self.O_QUESTION) if hasattr(self, 'O_QUESTION') else ''
        options = {}
        for letter, name in OPTION_OCR.items():
            if hasattr(self, name):
                text = OPTION_PREFIX.sub('', read(getattr(self, name)))
                if text:
                    options[letter] = text
        if question:
            logger.attr('Question(OCR)', question)
        return question, options

    def ask_ai(self, question: str = '', options: dict = None,
               wrong_options: list = None) -> dict:
        """
        截取弹窗区域交给 DeepSeek，题干/选项以文字一并给出
        """
        image = crop(self.device.image, DIALOG_AREA)
        if self.quiz_config.save_unknown_screenshot:
            os.makedirs(SHOT_DIR, exist_ok=True)
            path = f'{SHOT_DIR}/{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            cv2.imwrite(path, image)
        return self.client.answer_quiz(image, question=question, options=options,
                                       wrong_options=wrong_options)

    def current_index(self) -> int:
        """
        读「第 X/20 题」里的 X，读不到返回 0
        """
        results = self.O_PROGRESS.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(r.ocr_text for r in results)
        match = PROGRESS_RE.search(text)
        return int(match.group(1)) if match else 0


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
