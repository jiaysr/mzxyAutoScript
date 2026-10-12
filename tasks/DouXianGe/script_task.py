# This Python file uses the following encoding: utf-8
"""
斗仙阁（DouXianGe）

流程：进斗仙阁页 -> 读次数（今日剩余 + 特权额外）-> 循环：
  点列表第一个「挑战」按钮 -> 等进入斗仙阁战斗地图（右上角地点 == 斗仙阁）
  -> 重启游戏（回到主页面）-> 已完成次数 +1
次数来源：配置 challenge_count；0 = 把次数全部打完（含特权额外），
否则按配置次数打（游戏里次数先耗尽也停）。

结算重置期：每周日 23:00 ～ 周一 12:00，进入页面是空的（没有次数与列表），
此时不能挑战：跳过并排期到下周一 12:00 之后。
"""
from datetime import datetime, timedelta
from time import sleep

import re

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.DouXianGe.assets import DouXianGeAssets
from tasks.DouXianGe.page import page_douxian
from tasks.GameUi.game_ui import GameUi


class ScriptTask(GameUi, DouXianGeAssets):
    # 本任务在 ConfigManual.SCHEDULER_PRIORITY 中的名字（用于让路判断）
    SCHEDULER_NAME = 'DouXianGe'

    # 挑战按钮的匹配阈值（页面上有多个相同的按钮）
    CHALLENGE_THRESHOLD = 0.85
    # 战斗地图的地点名（右上角显示）
    MAP_NAME = '斗仙阁'
    # 点挑战后等进入战斗地图的超时时间
    ENTER_MAP_TIMEOUT = 90
    # 超时前仍在斗仙阁页面时，每隔多久重点一次挑战按钮
    ENTER_RETRY_INTERVAL = 6
    # 结算重置期：每周日 23:00 ～ 周一 12:00 页面清空不能挑战
    RESET_START_WEEKDAY = 6     # 周日
    RESET_START_HOUR = 23
    RESET_END_WEEKDAY = 0       # 周一
    RESET_END_HOUR = 12

    def run(self) -> None:
        # 结算重置期页面是空的，直接排期到下周一开放后
        if self.in_reset_period():
            self.schedule_reset_open()
            raise TaskEnd('DouXianGe')

        # 异常退出（未走完重启）可能停留在战斗地图里，先重启回主页面
        if self.map_in_location(self.MAP_NAME):
            logger.warning('当前已在斗仙阁战斗地图，先重启游戏')
            self.ui_restart_game()
        self.enter_douxian_page()
        count, bonus = self.read_challenge_counts()
        logger.attr('今日剩余挑战次数', count)
        logger.attr('特权额外挑战次数', bonus)
        if count is None:
            logger.warning('读不到「今日剩余挑战次数」')
        available = (count or 0) + (bonus or 0)

        config_count = int(self.config.dou_xian_ge.douxian_config.challenge_count or 0)
        target = available if config_count == 0 else min(config_count, available)
        logger.hr(f'斗仙阁：可用次数 {available}（含特权 {bonus}），本次计划挑战 {target} 次')

        done = 0
        while done < target:
            if done > 0:
                # 重启后回主页面，重新进斗仙阁页；次数被外部用掉/读完时提前结束
                self.enter_douxian_page()
                count, bonus = self.read_challenge_counts()
                if (count or 0) + (bonus or 0) <= 0:
                    logger.info('游戏内挑战次数已用完，提前结束')
                    break
            if not self.challenge_once():
                logger.warning('挑战流程失败，停止')
                break
            done += 1
            logger.attr('已完成挑战', f'{done}/{target}')

        self.end_run()
        raise TaskEnd('DouXianGe')

    # ---------------------------------------------------------------- 结算重置期
    @classmethod
    def in_reset_period(cls, now: datetime = None) -> bool:
        """是否处于结算重置期（周日 23:00 ～ 周一 12:00，页面清空不能挑战）"""
        now = now or datetime.now()
        if now.weekday() == cls.RESET_START_WEEKDAY and now.hour >= cls.RESET_START_HOUR:
            return True
        if now.weekday() == cls.RESET_END_WEEKDAY and now.hour < cls.RESET_END_HOUR:
            return True
        return False

    def schedule_reset_open(self, now: datetime = None) -> None:
        """排期到下周一 12:00 之后（结算结束开放）"""
        now = now or datetime.now()
        target = now.replace(hour=self.RESET_END_HOUR, minute=2, second=0, microsecond=0)
        if now.weekday() == self.RESET_START_WEEKDAY:
            target += timedelta(days=1)
        logger.warning(f'斗仙阁结算重置期（周日{self.RESET_START_HOUR}:00~周一{self.RESET_END_HOUR}:00），'
                       f'排期到 {target}')
        self.set_next_run(task='DouXianGe', target=target, success=None, finish=True, server=False)

    def end_run(self) -> None:
        """正常结束时的排期：结算期排到开放时间，否则默认下次周期"""
        if self.in_reset_period():
            self.schedule_reset_open()
        else:
            self.set_next_run(task='DouXianGe', success=True, finish=True)

    # ---------------------------------------------------------------- 导航
    def enter_douxian_page(self, timeout: int = 45) -> bool:
        self.ui_reset_current_page()
        if self.ui_goto(page_douxian, timeout=timeout):
            return True
        # 结算重置期页面清空，页面特征可能也一起消失，这不算卡死
        if self.in_reset_period():
            self.schedule_reset_open()
            raise TaskEnd('DouXianGe')
        raise GameStuckError('DouXianGe page does not appear')

    # ---------------------------------------------------------------- 读次数
    def read_ocr_number(self, rule, default=None):
        """OCR 规则区域里的最后一个数字（读不到返回 default）"""
        results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(item.ocr_text for item in results)
        logger.attr(rule.name, text)
        digits = re.findall(r'\d+', text)
        return int(digits[-1]) if digits else default

    def read_challenge_counts(self):
        """读 (今日剩余挑战次数, 特权额外挑战次数)"""
        self.screenshot()
        count = self.read_ocr_number(self.O_DOUXIAN_COUNT)
        bonus = self.read_ocr_number(self.O_DOUXIAN_BONUS, default=0)
        return count, bonus

    # ---------------------------------------------------------------- 找按钮
    def find_challenge_buttons(self) -> list:
        """返回页面上所有「挑战」按钮的中心坐标，按从上到下排序"""
        self.screenshot()
        matches = self.I_CHALLENGE_BUTTON.match_all_any(
            self.device.image, threshold=self.CHALLENGE_THRESHOLD)
        positions = []
        for score, x, y, w, h in matches:
            positions.append((x + w // 2, y + h // 2))
        positions.sort(key=lambda pos: pos[1])
        return positions

    def click_first_challenge(self) -> bool:
        """点击从上往下第一个「挑战」按钮"""
        positions = self.find_challenge_buttons()
        if not positions:
            logger.warning('未找到挑战按钮')
            return False
        x, y = positions[0]
        logger.info(f'点击第一个挑战按钮 ({x},{y})，共 {len(positions)} 个')
        self.device.click(x=x, y=y, control_name='DOUXIAN_CHALLENGE')
        self.device.click_record_clear()
        return True

    # ---------------------------------------------------------------- 进入战斗地图
    def wait_enter_battle_map(self, timeout: int = None) -> bool:
        """
        点击挑战后等待进入斗仙阁战斗地图（右上角地点名 == 斗仙阁）

        超时前若识别到仍停留在斗仙阁页面（按钮没点上/被取消），按间隔重试点击。
        """
        logger.hr('等待进入斗仙阁战斗地图')
        if timeout is None:
            timeout = self.ENTER_MAP_TIMEOUT
        timer = Timer(timeout).start()
        retry = Timer(self.ENTER_RETRY_INTERVAL).start()
        while not timer.reached():
            self.reset_records()
            if self.map_in_location(self.MAP_NAME):
                logger.info('已进入斗仙阁战斗地图')
                return True
            if retry.reached():
                retry.reset()
                self.screenshot()
                if self.appear(self.I_DOUXIAN_PAGE):
                    logger.warning('仍在斗仙阁页面，重新点击挑战按钮')
                    self.click_first_challenge()
            sleep(1)
        logger.warning(f'{timeout}s 内未进入斗仙阁战斗地图')
        return False

    def challenge_once(self) -> bool:
        """一次挑战：点第一个挑战按钮 -> 等进战斗地图 -> 重启游戏"""
        if not self.click_first_challenge():
            return False
        if not self.wait_enter_battle_map():
            raise GameStuckError('DouXianGe battle map does not appear')
        logger.info('开始重启游戏')
        self.ui_restart_game()
        logger.info('重启完成，本次挑战结束')
        return True


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
