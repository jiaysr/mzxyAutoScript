# This Python file uses the following encoding: utf-8
"""
每日答题

一局 20 题、每题 20 秒；点完选项会弹出「回答正确 / 回答错误」贴纸（约 5~7 秒），
贴纸消失后才出下一题；20 题结束出现「答题结束」图。

每题流程（question_time_limit 是每题的硬预算，读完题就开表）：
    1. 本地 OCR 题干 + 三个选项（免费）
    2. 题库按「题干 + 选项」定位（题干只差一两个字的题不会互相串），
       命中且已验证 -> 直接用题库答案，不调 API
    3. 否则交给 DeepSeek（deepseek-flash）看图作答，返回结构化 JSON；
       剩余预算不够问 AI 就不问了，直接走兜底抢答
    4. 点击对应选项 -> 等贴纸 -> 判定正确/错误
    5. 回写题库：答对转正、答错记录错误选项供下次排除，不管对错都入库
    6. 排除表是硬约束：进过排除表的选项，任何路径（题库/AI/兜底/抢答）都不会再点

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
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from module.paths import LOG_DIR
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main
from tasks.Quiz import bank_sync
from tasks.Quiz.assets import QuizAssets
from tasks.Quiz.deepseek_client import DeepSeekClient
from tasks.Quiz.question_bank import QuestionBank

TASK_DIR = Path(__file__).resolve().parent
BANK_FILE = str(TASK_DIR / 'bank' / 'quiz_zh.json')
UNKNOWN_FILE = str(TASK_DIR / 'bank' / 'quiz_unknown.jsonl')
SHOT_DIR = str(LOG_DIR / 'quiz')

# 背包里的答题券名称（bag_find_item 走 OCR 名字匹配）
BAG_ITEM_NAME = '趣味答题券'

# 交给 AI 的弹窗区域 (x, y, w, h)：题号 + 题干 + 三个选项，不含右侧答题榜
DIALOG_AREA = (60, 120, 790, 390)

# 抢答预留秒数：预算扣到这个值就不再做任何耗时判断，直接用兜底策略把题答上
RUSH_RESERVE = 1.5
# 剩余预算低于这个秒数就别问 AI 了（问了也来不及点）
AI_MIN_BUDGET = 3.0

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


class ScriptTask(GameUi, QuizAssets):
    # 答题界面刚出现时题目还在刷新，等页面稳定再开始读题
    page_ready_wait = 2

    def run(self) -> None:
        self.check_assets()

        self.bank = QuestionBank(BANK_FILE, UNKNOWN_FILE).load()
        self.bank_pull()
        self.client = DeepSeekClient(
            api_key=self.quiz_config.api_key or os.environ.get('DEEPSEEK_API_KEY', ''),
            model=self.quiz_config.model,
            timeout=self.quiz_config.api_timeout,
            use_thinking=self.quiz_config.use_thinking,
        )
        self.device.stuck_record_add('QUIZ')

        if self.enter_quiz_by_ticket():
            self.answer_loop()
            self.finish_quiz()
        else:
            logger.warning('答题券不可用（可能今天已经答过），跳过本次答题')

        self.bank_push()
        self.back_to_main()
        self.schedule_next_day()
        raise TaskEnd('Quiz')

    # ---------------------------------------------------------------- 题库同步
    def bank_pull(self) -> None:
        """
        答题前把别的设备学到的题合并进来（同步失败只告警，不影响答题）
        """
        repo_dir = self.quiz_config.bank_repo_dir.strip()
        if not repo_dir:
            return
        try:
            stats = bank_sync.pull(BANK_FILE, repo_dir,
                                   url=self.quiz_config.bank_repo_url.strip())
        except Exception as e:
            logger.warning(f'题库同步失败（不影响答题）: {e}')
            return
        if stats.get('changed'):
            self.bank.load()

    def bank_push(self) -> None:
        """
        答题后把本机新学的题推到交换仓库（同步失败只告警）
        """
        repo_dir = self.quiz_config.bank_repo_dir.strip()
        if not repo_dir:
            return
        try:
            bank_sync.push(BANK_FILE, repo_dir,
                           url=self.quiz_config.bank_repo_url.strip(),
                           device=self.config.config_name)
        except Exception as e:
            logger.warning(f'题库同步失败（本机题库不受影响）: {e}')

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
    def enter_quiz_by_ticket(self, timeout: int = 15) -> bool:
        """
        进入物品-背包页找到「趣味答题券」，点开详情面板后立即使用，进入答题界面
        :return: 答题界面是否出现
        """
        logger.hr('Use quiz ticket')
        if not self.bag_use_item(BAG_ITEM_NAME):
            logger.warning(f'背包里没有找到 [{BAG_ITEM_NAME}] 或没有点到立即使用')
            return False
        return self.wait_quiz_page(timeout=timeout)

    def back_to_main(self) -> None:
        """
        答题结束后回到主页面
        关掉答题弹窗后界面已经不在背包页（缓存里还留着背包页），先作废再重新识别
        """
        self.ui_reset_current_page()
        if not self.ui_goto(page_main, timeout=30):
            logger.warning('返回主页面失败')

    def wait_quiz_page(self, timeout: int = 30) -> bool:
        """
        等待答题界面出现
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
                logger.warning('答题界面没有出现')
                return False
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
        limit = self.quiz_config.question_time_limit
        answered = 0

        while answered < total:
            if not self.wait_question_ready(timeout=30):
                logger.warning('等不到下一题，结束答题')
                break
            answered += 1
            logger.hr(f'第 {answered}/{total} 题', level=2)
            # 每题的限时从这里开始算：读题、问 AI、点击都要在这个预算内完成，
            # 预算见底还没定下答案就抢答，别让题目干等着超时
            self.question_timer = Timer(limit).start()
            self.answer_one()
            logger.attr(f'第 {answered} 题用时', f'{self.question_timer.current():.1f}s / {limit}s')
            self.wait_feedback_done(timeout=15)

        logger.info(f'共作答 {answered} 题')

    # ---------------------------------------------------------------- 时间预算
    def question_budget_left(self) -> float:
        """
        本题还剩多少秒；没在计时（比如单测直接调 answer_one）返回 inf
        """
        timer = getattr(self, 'question_timer', None)
        if timer is None:
            return float('inf')
        return max(0.0, timer.limit - timer.current())

    def ai_budget_enough(self) -> bool:
        """
        剩余预算够不够问一次 AI：不够就别问了，直接兜底作答，至少把题答上
        """
        return self.question_budget_left() - RUSH_RESERVE >= AI_MIN_BUDGET

    def ai_timeout(self) -> float:
        """
        AI 单次调用超时：不超过配置的 api_timeout，也不超过本题剩余预算（要留出抢答时间）
        """
        timeout = max(1.0, float(self.quiz_config.api_timeout))
        left = self.question_budget_left()
        if left == float('inf'):
            return timeout
        return max(1.0, min(timeout, left - RUSH_RESERVE))

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
        答一题：

        1. 本地 OCR 题干 + 选项，题库按「题干 + 选项」定位（相似题干不会串答案）
        2. 题库能给答案且没被排除 -> 直接用（答完仍读反馈校验）
        3. 否则落库 + 问 AI（剩余预算不够就不问了）
        4. AI 也没给出可点选项 -> 题库兜底 -> 兜底策略抢答
        任何一步选出来的选项都不会是排除表里的
        """
        self.screenshot()
        question, options = self.ocr_locally()
        record, score = self.bank.find(question, options)

        # 1) 题库命中：已验证的答案（或未验证但配置为不重问）直接用
        letter, answer_text = self.bank_letter(record, options, strict=True)
        if letter:
            logger.info(f'题库命中({score:.2f}): {answer_text} -> {letter}')
            self.click_option(letter)
            feedback = self.read_feedback()
            # 命中也走校验：答对保持/转正，答错降级并记录错误选项
            self.bank.update_by_feedback(question, options.get(letter, answer_text), feedback,
                                         record=record, options=options)
            return

        # 2) 先把题目落库（API 失败也不丢），再问 AI
        if question and self.quiz_config.learn_from_llm:
            record = self.bank.ensure_entry(question, options)
        wrong_options = list(record.get('wrong') or []) if record else []

        if self.ai_budget_enough():
            ai = self.ask_ai(question=question, options=options, wrong_options=wrong_options)
        else:
            logger.warning(f'本题只剩 {self.question_budget_left():.1f}s，来不及问 AI，直接兜底作答')
            ai = None

        if ai is not None:
            letter, answer_text = self.pick_ai_option(question, ai, options, wrong_options)
            if letter:
                self.click_option(letter)
                feedback = self.read_feedback()
                self.learn(question, options, answer_text or options.get(letter, ''), feedback, record)
                return
            logger.warning(f'AI 没能给出可点的选项: answer={ai.get("answer")!r} '
                           f'answer_text={answer_text!r} options={options}')

        # 3) 兜底：题库里还没被排除的答案 -> 抢答（同样遵守排除表）
        letter, answer_text = self.pick_bank_option(record, options)
        if letter:
            logger.info(f'题库兜底作答: {answer_text} -> {letter}')
            self.click_option(letter)
            # 兜底也读反馈回写：随机选对时能把题库答案转正
            feedback = self.read_feedback()
            self.bank.update_by_feedback(question, options.get(letter, answer_text), feedback,
                                         record=record, options=options)
            return

        logger.warning('AI 不可用（题目已落库，下次会重试）' if ai is None else 'AI 与题库都给不出答案')
        self.bank.append_unknown(question, options)
        self.fallback_pick(options, wrong_options, record)

    def bank_letter(self, record: dict, options: dict, strict: bool = True) -> tuple:
        """
        题库里的答案能不能直接拿去点（排除表是硬约束，被排除的答案一律不用）

        :param strict: True 只认「已验证」或「配置允许的未验证答案」（正常答题）；
                       False 连未验证的答案也用（AI 不可用时的兜底）
        :return: (letter, answer_text)，用不了返回 ('', '')
        """
        if not record or not options:
            return '', ''
        answer = record.get('answer') or ''
        if not answer:
            return '', ''
        if strict and not record.get('verified') and self.quiz_config.ask_again_if_unverified:
            return '', ''
        letter = self.bank.option_letter(options, answer, record.get('wrong') or [])
        if not letter:
            return '', ''
        return letter, answer

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

    def fallback_pick(self, options: dict = None, wrong_options: list = None,
                      record: dict = None) -> None:
        """
        兜底（抢答）选一个选项：永不点排除表里的选项

        - 选项文字读全了：只在没被排除的选项里挑
        - 已知选项全被排除：说明排除表和本题自相矛盾，清空重来（见 QuestionBank.pick_letter）
        - 选项文字完全没读出来：没有排除表就闭眼选一个，有排除表则不作答（无法核对，宁可空着）
        """
        mode = self.quiz_config.unknown_fallback
        letter = self.bank.pick_letter(options, wrong_options, mode=mode, record=record)
        if not letter:
            logger.warning(f'兜底策略：{mode} -> 没有可点的选项（不作答）')
            return
        logger.warning(f'兜底策略：{mode} -> {letter}')
        self.click_option(letter)

    def pick_ai_option(self, question: str, ai: dict, options: dict,
                       wrong_options: list) -> tuple:
        """
        从 AI 结果解析可点的选项

        - AI 选了已验证错误的选项时，带着剩余选项重问一次，仍不可用就在剩余选项里随机
        - 该选项文字没读出来时无法核对排除表：只有排除表里没有「对不上任何已知选项」的条目
          才敢点（有的话，被排除的多半就是这个没读出来的选项）
        :return: (letter, answer_text)，无法确定返回 ('', answer_text)
        """
        answer_text = ai.get('answer_text') or ''
        letter = self.bank.match_option(options, answer_text) if answer_text else ''
        if not letter:
            candidate = (ai.get('answer') or '').strip().upper()
            if candidate in (options or {}):
                letter = candidate
            elif candidate in OPTION_CLICKS:
                if not wrong_options:
                    # 选项文字没读全，但也没有排除表要核对，直接信模型给的字母
                    return candidate, answer_text
                # 有排除表却读不到这个选项的文字：核对不了，当成不安全的选点往下走
                letter = candidate
        if not letter:
            return '', answer_text

        if not options.get(letter) and self.bank.orphan_wrong(options, wrong_options):
            logger.warning(f'AI 选了 {letter}，但该选项文字没读到、排除表里还有对不上选项的条目，'
                           f'改从剩余选项里挑')
            letter = ''
        if letter and not self.bank.find_excluded(options.get(letter, ''), wrong_options):
            return letter, answer_text

        remaining = {k: v for k, v in (options or {}).items()
                     if v and not self.bank.find_excluded(v, wrong_options)}
        if not remaining:
            logger.warning('AI 选了已排除的选项，且所有选项都已被排除')
            return '', answer_text
        logger.warning(f'AI 选了已排除的选项 {answer_text!r}，重问剩余选项 {sorted(remaining)}')
        retry = None
        if self.ai_budget_enough():
            retry = self.ask_ai(question=question, options=remaining, wrong_options=wrong_options)
        else:
            logger.warning(f'本题只剩 {self.question_budget_left():.1f}s，不重问 AI')
        if retry is not None:
            retry_text = retry.get('answer_text') or ''
            retry_letter = self.bank.match_option(remaining, retry_text) if retry_text else ''
            if not retry_letter:
                candidate = (retry.get('answer') or '').strip().upper()
                retry_letter = candidate if candidate in remaining else ''
            if retry_letter:
                logger.info(f'重问得到: {retry_letter} {retry_text}')
                return retry_letter, retry_text
        letter = random.choice(list(remaining))
        logger.warning(f'重问不可用，在剩余选项里随机: {letter}')
        return letter, remaining[letter]

    def pick_bank_option(self, record: dict, options: dict) -> tuple:
        """
        AI 不可用时的题库兜底：优先用题库已有答案（被排除的不用），
        否则从剩余选项里随机，避免把同一个错误答案再点一遍
        :return: (letter, answer_text)，选不出返回 ('', '')
        """
        letter, answer = self.bank_letter(record, options, strict=False)
        if letter:
            return letter, answer
        if not (record and options):
            return '', ''
        letter = self.bank.pick_letter(options, record.get('wrong') or [],
                                       mode='random', record=record)
        if letter:
            logger.warning(f'题库答案不可用，从剩余选项里选: {letter}')
            return letter, options.get(letter, '')
        return '', ''

    # ---------------------------------------------------------------- 学习
    def learn(self, question: str, options: dict, answer_text: str,
              feedback: str, record: dict = None) -> None:
        """
        用 AI 答案 + 游戏反馈回写题库（不管对错都写）
        :param record: 本题已定位到的记录，直接写它，不再按题干重新匹配
        """
        if not question:
            return
        if self.quiz_config.learn_from_llm:
            record = self.bank.upsert(question, options, answer_text, record=record)
        self.bank.update_by_feedback(question, answer_text, feedback,
                                     record=record, options=options)
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

        超时按本题剩余预算收窄（见 ai_timeout）：宁可这次问不出来走兜底，
        也不要卡在那里把整道题拖到超时
        """
        image = crop(self.device.image, DIALOG_AREA)
        if self.quiz_config.save_unknown_screenshot:
            os.makedirs(SHOT_DIR, exist_ok=True)
            path = f'{SHOT_DIR}/{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            cv2.imwrite(path, image)
        return self.client.answer_quiz(image, question=question, options=options,
                                       wrong_options=wrong_options,
                                       timeout=self.ai_timeout())

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

