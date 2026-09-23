# This Python file uses the following encoding: utf-8
"""
参与战车（仙盟战车）：

1. 每天 start_time（默认 20:00）开放，提前 advance_time（默认 5 分钟）开始准备：
   游戏不在线就启动并登录，在线就切回主页面，保证开放时游戏在线且停留在主页面
2. 到点后每 check_interval 秒（默认 2s）检测一次主页面文字区域，
   识别到 keyword（默认「仙盟战车」）就点击参与区域
3. 点击后等待 2 秒关闭游戏，离线 offline_time（默认 5 分钟）后重新启动游戏并登录到主页面
4. 检测超时 check_timeout 秒（默认 20 分钟）视为错过，同样标记任务完成
5. 无论是否参与成功，任务都算完成，并排到第二天的准备时间
"""
import difflib
from datetime import datetime, timedelta

from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.WarChariot.assets import WarChariotAssets

# OCR 形近字容忍的相似度阈值
SIMILARITY = 0.75


def text_match(text: str, keyword: str) -> bool:
    """
    文案匹配：包含关键字，或等长滑窗相似度达标（容忍 OCR 形近字误识）
    """
    if not text or not keyword:
        return False
    if keyword in text:
        return True
    for index in range(len(text) - len(keyword) + 1):
        window = text[index:index + len(keyword)]
        if window[0] != keyword[0]:
            continue
        if difflib.SequenceMatcher(None, window, keyword).ratio() >= SIMILARITY:
            return True
    return False


class ScriptTask(GameUi, WarChariotAssets):
    # 点击参与后等待关闭游戏的间隔（秒）
    click_wait = 2

    def run(self) -> None:
        event_time = self.event_datetime()
        prepare_time = event_time - self.advance()
        deadline = event_time + timedelta(seconds=self.config.war_chariot.war_chariot_config.check_timeout)
        now = datetime.now()
        logger.attr('War chariot', f'{event_time.strftime("%Y-%m-%d %H:%M:%S")}')

        if now < prepare_time:
            # 任务被提前排到（如隔天启动脚本、任务长期未运行），排到准备时间再跑，避免提前等待
            logger.info(f'未到准备时间 {prepare_time.strftime("%H:%M:%S")}，先排期')
            self.set_next_run(task='WarChariot', target=prepare_time, success=None, finish=True, server=False)
            raise TaskEnd('WarChariot')

        if now >= deadline:
            logger.warning(f'已超过检测截止时间 {deadline.strftime("%H:%M:%S")}，今天跳过')
            self.schedule_next_run()
            raise TaskEnd('WarChariot')

        self.prepare_game()
        self.wait_until_event(event_time)

        if self.wait_chariot(deadline):
            self.join_chariot()
        else:
            logger.warning(f'检测超时，未出现「{self.config.war_chariot.war_chariot_config.keyword}」，按完成处理')

        self.schedule_next_run()
        raise TaskEnd('WarChariot')

    # ---------------------------------------------------------------- 调度
    def event_datetime(self) -> datetime:
        """
        今天的开放时间
        """
        start_time = self.config.war_chariot.war_chariot_config.start_time
        return datetime.combine(datetime.now().date(), start_time)

    def advance(self) -> timedelta:
        """
        提前准备的时间
        """
        advance_time = self.config.war_chariot.war_chariot_config.advance_time
        return timedelta(hours=advance_time.hour,
                         minutes=advance_time.minute,
                         seconds=advance_time.second)

    def schedule_next_run(self) -> None:
        """
        排到第二天的准备时间（开放时间 - 提前时间）
        """
        target = self.event_datetime() - self.advance()
        if target <= datetime.now():
            target = self.event_datetime() + timedelta(days=1) - self.advance()
        logger.attr('Next war chariot prepare', target)
        self.set_next_run(task='WarChariot', target=target, success=None, finish=True, server=False)

    # ---------------------------------------------------------------- 准备
    def prepare_game(self) -> None:
        """
        提前准备：确保游戏在线并停留在主页面
        游戏没运行就重启登录，运行中就先尝试切回主页面，页面识别不了再重启
        """
        logger.hr('准备游戏（在线 + 主页面）')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            logger.info('游戏已登录到主页面')
            return

        self.ui_reset_current_page()
        try:
            if self.ui_goto(page_main, timeout=30):
                logger.info('游戏在线且在主页面')
                return
            logger.warning('无法切回主页面，重启游戏')
        except (GameNotRunningError, GamePageUnknownError):
            logger.warning('当前页面异常，重启游戏')
        self.ui_restart_game()
        logger.info('游戏已登录到主页面')

    def wait_until_event(self, event_time: datetime) -> None:
        """
        等待到开放时间（等待期间保持屏幕不动，只清空卡死记录）
        """
        logger.hr('等待开放时间')
        while 1:
            remain = (event_time - datetime.now()).total_seconds()
            if remain <= 0:
                break
            logger.attr('War chariot', f'距 {event_time.strftime("%H:%M:%S")} 还有 {int(remain)}s')
            self.device.sleep(min(2, remain))
            self.reset_records()
        logger.info(f'开放时间到 {event_time.strftime("%H:%M:%S")}')

    # ---------------------------------------------------------------- 检测与参与
    def detect_texts(self) -> list:
        """
        识别检测区域的文字
        """
        self.screenshot()
        return [result.ocr_text
                for result in self.O_CHARIOT_TEXT.detect_and_ocr(self.device.image, logDisplay=False)]

    def wait_chariot(self, deadline: datetime) -> bool:
        """
        每 check_interval 秒检测一次，识别到关键字返回 True
        超过截止时间返回 False（按超时处理）
        """
        logger.hr('检测仙盟战车')
        cfg = self.config.war_chariot.war_chariot_config
        count = 0
        while 1:
            started = datetime.now()
            texts = self.detect_texts()
            count += 1
            if any(text_match(text, cfg.keyword) for text in texts):
                logger.info(f'检测到「{cfg.keyword}」：{texts}')
                return True
            if datetime.now() >= deadline:
                logger.warning(f'检测超时（截止 {deadline.strftime("%H:%M:%S")}），最后识别：{texts}')
                return False
            if count % 15 == 0:
                logger.info(f'已检测 {count} 次，最近识别：{texts}')
            # 保持 check_interval 的检测频率（截图与 OCR 的耗时算在间隔内）
            self.reset_records()
            self.device.sleep(max(0, cfg.check_interval - (datetime.now() - started).total_seconds()))

    def join_chariot(self) -> None:
        """
        点击参与区域 -> 等待 2 秒关闭游戏 -> 等待离线时长 -> 启动游戏到主页面
        """
        logger.hr('参与战车')
        x, y = self.C_CHARIOT_JOIN.coord()
        logger.info(f'点击参与区域 ({x}, {y})')
        self.device.click(x=x, y=y, control_name=self.C_CHARIOT_JOIN.name)

        self.device.sleep(self.click_wait)
        logger.info('关闭游戏')
        self.device.app_stop()

        self.wait_offline()

        logger.info('离线结束，启动游戏')
        self.ui_restart_game()
        logger.info('游戏已登录到主页面，任务完成')

    def wait_offline(self) -> None:
        """
        等待游戏离线的时长
        """
        offline_time = self.config.war_chariot.war_chariot_config.offline_time
        seconds = offline_time.hour * 3600 + offline_time.minute * 60 + offline_time.second
        logger.hr(f'等待游戏离线 {seconds}s')
        end = datetime.now() + timedelta(seconds=seconds)
        while 1:
            remain = (end - datetime.now()).total_seconds()
            if remain <= 0:
                break
            logger.attr('Offline', f'剩余 {int(remain)}s')
            self.device.sleep(min(30, remain))


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
