# This Python file uses the following encoding: utf-8
"""
人机验证（算术题）弹窗处理，由 GlobalGame 混入：

弹窗组成：红字标题「人机验证」+ 一行算式（如 60 + 4 = ?）+ 十个选项
（左列 A~E、右列 F~J，每项「字母: 数字」）+ 底部倒计时。

处理：识别算式算出答案 -> 找数字相同的选项 -> 点它所在的选项格。
所有任务的 screenshot 都会调用 handle_captcha（见 tasks/base_task.py）；
答不掉时按卡死处理（框架会重启游戏），并推一条通知提醒人工介入。

素材（tasks/GlobalGame/res/）：
  I_CAPTCHA_DIALOG     标题红字，判断弹窗在不在（模板匹配，便宜）
  O_CAPTCHA_EXPRESSION 算式行（Single 整行识别：'60 + 4 = ?'）
  O_CAPTCHA_OPTIONS    选项区（Full，每项一个框：'A:81'，I 常被认成 1）
"""
import re
from typing import TYPE_CHECKING

from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.GlobalGame.assets import GlobalGameAssets

if TYPE_CHECKING:
    from module.config.config import Config
    from module.device.device import Device


class CaptchaHandler(GlobalGameAssets):
    """人机验证弹窗：识别算式、算出答案、点中对应选项"""

    if TYPE_CHECKING:
        # 以下成员由子类 BaseTask 提供，这里仅作类型声明（运行时不生效）
        config: Config
        device: Device

        def appear(self, target, interval: float = None, threshold: float = None) -> bool: ...

    # 弹窗检测间隔（秒）：模板匹配很便宜，但没必要每帧都做
    captcha_check_interval: float = 1.0
    # 一次处理最多答几轮（点错 / 连出题时重新识别再答）
    CAPTCHA_MAX_ROUND = 3
    # 点完选项等界面反馈的时间（秒）
    CAPTCHA_CLICK_WAIT = 1.5
    # 选项格中心的 x（左列 A~E / 右列 F~J）与五行中心 y（1280x720 实测）
    CAPTCHA_COL_X = (466, 731)
    CAPTCHA_ROW_Y = (273, 321, 370, 420, 468)
    # 选项框中心与行中心的偏差上限（行距 48，取一半再紧一点，防串行）
    CAPTCHA_ROW_TOLERANCE = 22
    # 选项字母按位置定：左列自上而下 A~E，右列 F~J
    CAPTCHA_LETTERS = ('ABCDE', 'FGHIJ')

    def handle_captcha(self) -> None:
        """
        检测到人机验证弹窗就答题，调用前 device.image 需为最新截图
        """
        timer = getattr(self, '_captcha_timer', None)
        if timer is None:
            timer = Timer(self.captcha_check_interval).start()
            self._captcha_timer = timer
        if not timer.reached():
            return
        timer.reset()

        if not self.appear(self.I_CAPTCHA_DIALOG):
            return
        self.solve_captcha()

    def solve_captcha(self) -> None:
        """
        答题直到弹窗消失：识别算式 -> 算答案 -> 点对应选项格

        每轮点完等 CAPTCHA_CLICK_WAIT 秒再看弹窗是否消失：消失即解完；
        还在就重识别再答（连出题 / 上一轮点偏了），答满 CAPTCHA_MAX_ROUND 轮
        仍未解掉就按卡死处理（框架会重启游戏），并通知人工介入
        """
        logger.hr('人机验证')
        for round_index in range(1, self.CAPTCHA_MAX_ROUND + 1):
            if round_index > 1:
                # 上一轮的界面反馈：弹窗关掉就是解完了，否则重识别再答
                self.device.sleep(self.CAPTCHA_CLICK_WAIT)
                self.device.screenshot()
                if not self.appear(self.I_CAPTCHA_DIALOG):
                    logger.info('Captcha solved')
                    return
            letter = self.captcha_answer_letter()
            if letter is None:
                continue
            x, y = self.captcha_option_coord(letter)
            logger.info(f'人机验证点选项 {letter} ({x},{y})')
            self.device.click(x=x, y=y, control_name=f'captcha_option_{letter}')
            self.device.click_record_clear()

        # 最后一轮点完再确认一次（循环里没机会确认）
        self.device.sleep(self.CAPTCHA_CLICK_WAIT)
        self.device.screenshot()
        if not self.appear(self.I_CAPTCHA_DIALOG):
            logger.info('Captcha solved')
            return

        logger.error('人机验证自动答题失败，请人工处理')
        self.config.notifier.push(title='人机验证',
                                  content=f'<{self.config.config_name}> 自动答题失败，请人工处理')
        raise GameStuckError('Captcha not solved')

    # ---------------------------------------------------------------- 识别
    def captcha_answer_letter(self) -> str | None:
        """
        识别并计算一次：返回答案所在选项的字母，任何一步失败返回 None
        """
        expression = self.captcha_parse_expression()
        if expression is None:
            return None
        answer = self.captcha_compute(expression)
        if answer is None:
            logger.warning(f'人机验证算式结果不是整数: {expression}')
            return None
        options = self.captcha_parse_options()
        if not options:
            logger.warning('人机验证选项识别失败')
            return None
        logger.attr('人机验证算式', f'{expression[0]} {expression[1]} {expression[2]} = {answer}')
        logger.attr('人机验证选项',
                    ' '.join(f'{letter}:{options[letter]}' for letter in sorted(options)))
        for letter in 'ABCDEFGHIJ':
            if options.get(letter) == answer:
                return letter
        logger.warning(f'人机验证选项里没有答案 {answer}')
        return None

    def captcha_parse_expression(self) -> tuple | None:
        """
        识别算式行，返回 (左操作数, 运算符, 右操作数)
        """
        texts = [item.ocr_text for item in
                 self.O_CAPTCHA_EXPRESSION.detect_and_ocr(self.device.image, logDisplay=False)]
        for text in texts:
            match = re.search(r'(\d+)\s*([+\-*/xX×÷])\s*(\d+)', text)
            if match:
                return int(match.group(1)), match.group(2), int(match.group(3))
        logger.warning(f'人机验证算式识别失败: {texts}')
        return None

    @staticmethod
    def captcha_compute(expression: tuple) -> int | None:
        """
        算式求值：支持 + - ×(x/X/*) ÷(/)，除不尽或运算符不认识返回 None
        """
        left, operator, right = expression
        if operator == '+':
            return left + right
        if operator == '-':
            return left - right
        if operator in ('x', 'X', '*', '×'):
            return left * right
        if operator in ('/', '÷'):
            if right == 0 or left % right:
                return None
            return left // right
        return None

    def captcha_parse_options(self) -> dict:
        """
        识别选项区，返回 {字母: 数字}

        字母按位置定（左列 A~E / 右列 F~J，行按 y 对齐），不认 OCR 出的字母
        （实测 I 会被识别成 1）；数字取框里最后一串数字
        """
        roi_x, roi_y = self.O_CAPTCHA_OPTIONS.roi[0], self.O_CAPTCHA_OPTIONS.roi[1]
        result = {}
        for item in self.O_CAPTCHA_OPTIONS.detect_and_ocr(self.device.image, logDisplay=False):
            numbers = re.findall(r'\d+', item.ocr_text)
            if not numbers:
                continue
            cx = sum(point[0] for point in item.box) / len(item.box) + roi_x
            cy = sum(point[1] for point in item.box) / len(item.box) + roi_y
            letter = self.captcha_letter_at(cx, cy)
            if letter is None:
                continue
            result[letter] = int(numbers[-1])
        return result

    def captcha_letter_at(self, x: float, y: float) -> str | None:
        """
        选项框中心坐标 -> 字母：左列 A~E、右列 F~J
        行列都对不上（OCR 框乱飘）返回 None
        """
        row = None
        for index, center in enumerate(self.CAPTCHA_ROW_Y):
            if abs(y - center) <= self.CAPTCHA_ROW_TOLERANCE:
                row = index
                break
        if row is None:
            logger.warning(f'人机验证选项不在任何一行: y={y:.0f}')
            return None
        column = 0 if abs(x - self.CAPTCHA_COL_X[0]) < abs(x - self.CAPTCHA_COL_X[1]) else 1
        return self.CAPTCHA_LETTERS[column][row]

    def captcha_option_coord(self, letter: str) -> tuple:
        """选项格中心坐标：按字母取所在行列中心"""
        for index, letters in enumerate(self.CAPTCHA_LETTERS):
            if letter in letters:
                return int(self.CAPTCHA_COL_X[index]), int(self.CAPTCHA_ROW_Y[letters.index(letter)])
        raise ValueError(f'Unknown captcha option letter: {letter}')
