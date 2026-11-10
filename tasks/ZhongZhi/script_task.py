# This Python file uses the following encoding: utf-8
"""
种植（ZhongZhi）

功能流程：
    主页面 -> 角色面板 -> 辅助/幻化页 -> 顶部 tab「种植」-> 种植页面
    -> 点击一键种植
    -> 选择种子弹窗 / 不可种植弹窗
    -> 有种子则选第一个种子、确认、关闭完成弹窗，再继续一键种植
    -> 不可种植弹窗出现时读取土地倒计时，按倒计时安排下次运行

无种子（8 个种子格全空）时发送通知，点取消回主页面并判定任务失败。
"""
import re
from datetime import datetime, timedelta

import cv2

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd, TaskFailed
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main
from tasks.ZhongZhi.assets import ZhongZhiAssets
from tasks.ZhongZhi.page import page_zhongzhi


class ScriptTask(GameUi, ZhongZhiAssets):
    SCHEDULER_NAME = 'ZhongZhi'
    # 一键种植循环最多执行几次，防止异常状态下无限循环
    MAX_PLANT_ROUNDS = 20
    # 点击一键种植后等待「选择种子/不可种植」弹窗的时间
    PLANT_RESPONSE_TIMEOUT = 10
    # 点击确认后等待「收获成功」完成弹窗的时间
    SUCCESS_POPUP_TIMEOUT = 8
    # 倒计时结束后多等几秒，避免游戏时间与本地时间有偏差
    COOLDOWN_MARGIN_SECONDS = 10
    # 倒计时 OCR 失败时的兜底等待时间（秒）
    DEFAULT_COOLDOWN_SECONDS = 8 * 3600
    # 选择种子弹窗的 8 个格子（4 列 x 2 行），按从左到右、从上到下排序
    SEED_SLOT_ROIS = (
        (416, 261, 72, 72), (518, 261, 72, 72),
        (620, 261, 72, 72), (721, 261, 72, 72),
        (416, 366, 72, 66), (518, 366, 72, 66),
        (620, 366, 72, 66), (721, 366, 72, 66),
    )
    # 倒计时格式：时'分'秒，例如 07'59'33
    COOLDOWN_PATTERN = re.compile(r"(\d{1,2})\s*['’]\s*(\d{1,2})\s*['’]\s*(\d{1,2})")

    def run(self) -> None:
        logger.hr('种植')
        success = False
        target = None
        try:
            self.ui_reset_current_page()
            if not self.ui_goto(page_zhongzhi, timeout=60):
                raise GameStuckError('Zhongzhi page does not appear')
            logger.info('已进入种植页面')
            target = self.do_planting_loop()
            success = True
        finally:
            try:
                self.back_to_main()
            finally:
                # 正常/异常都重排：成功时按土地倒计时，失败时按失败间隔
                self.set_next_run(task='ZhongZhi', success=success, finish=True,
                                  target=target, server=False)
        raise TaskEnd('ZhongZhi')

    def back_to_main(self) -> None:
        """关闭种植页面回主页面（失败只告警，不覆盖任务原始异常）"""
        try:
            # 先处理种植相关弹窗，避免 ui_goto 走通用未知页面关闭逻辑时每次等 3 秒
            self.screenshot()
            if self.appear(self.I_SEED_POPUP):
                logger.info('返回主页面：关闭选择种子弹窗')
                self.click(self.C_SEED_CANCEL)
                self.device.click_record_clear()
                self.device.sleep(0.5)

            for _ in range(2):
                self.screenshot()
                if not (self.appear(self.I_PLANT_SUCCESS)
                        or self.appear(self.I_PLANT_UNAVAILABLE)):
                    break
                self.appear_then_click(self.I_PLANT_POPUP_CLOSE)
                self.device.click_record_clear()
                self.device.sleep(0.5)

            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=30):
                logger.warning('无法从种植页面返回主页面')
        except TaskEnd:
            # 死亡/掉线续跑会抛 TaskEnd，不能被清理逻辑吞掉
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f'返回主页面失败：{e}')

    # ---------------------------------------------------------------- 种植循环
    def do_planting_loop(self) -> datetime | None:
        """
        循环点击一键种植，直到出现「没有可种植新的土地」

        :return: 土地倒计时结束后的下次运行时间
        """
        logger.hr('一键种植循环')
        for round_no in range(1, self.MAX_PLANT_ROUNDS + 1):
            self.reset_records()
            self.screenshot()
            if not self.appear(self.I_ZHONGZHI_PAGE) and not self.appear(self.I_ZHONGZHI_HELP):
                raise GameStuckError('Zhongzhi page does not appear')

            logger.info(f'点击一键种植（{round_no}/{self.MAX_PLANT_ROUNDS}）')
            # BaseTask.click 在不传 interval 时返回 False，不能把返回值当点击结果
            self.click(self.C_ONE_KEY_PLANT)
            self.device.click_record_clear()

            response = self.wait_plant_response()
            if response == 'seed':
                self.handle_seed_popup()
                continue
            if response == 'unavailable':
                logger.info('当前没有可种植新的土地，种植完成')
                if not self.close_plant_popup():
                    logger.warning('无法关闭不可种植弹窗')
                self.device.sleep(0.5)
                return self.land_cooldown_target()

            raise GameStuckError('One-key plant response does not appear')

        raise GameStuckError('Too many planting rounds')

    def wait_plant_response(self) -> str | None:
        """等待一键种植后的弹窗：返回 seed / unavailable / None"""
        timer = Timer(self.PLANT_RESPONSE_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_SEED_POPUP):
                return 'seed'
            if self.appear(self.I_PLANT_UNAVAILABLE):
                return 'unavailable'
            self.device.sleep(0.2)
        return None

    # ---------------------------------------------------------------- 种子弹窗
    def handle_seed_popup(self) -> None:
        """处理选择种子弹窗：无种子则通知并失败，有种子则种第一个"""
        logger.hr('选择种子')
        self.reset_records()
        self.screenshot()

        empty_count = self.count_empty_seed_slots()
        logger.attr('空种子格', f'{empty_count}/{len(self.SEED_SLOT_ROIS)}')
        if empty_count >= len(self.SEED_SLOT_ROIS):
            self.notify_no_seed()
            logger.info('点击取消关闭选择种子弹窗')
            self.click(self.C_SEED_CANCEL)
            self.device.click_record_clear()
            self.device.sleep(0.5)
            raise TaskFailed('种植无种子')

        slot = self.first_seed_slot()
        if slot is None:
            # 理论上 empty_count < 8 时不会走到这里，兜底按无种子处理
            self.notify_no_seed()
            self.click(self.C_SEED_CANCEL)
            self.device.click_record_clear()
            raise TaskFailed('种植无种子')

        logger.info(f'点击第一个种子位置 {slot}')
        self.device.click(x=slot[0], y=slot[1], control_name='zhongzhi_seed_slot')
        self.device.click_record_clear()
        self.device.sleep(0.5)

        # 点击后悬停提示才会出现，重新截图再识别种子名称
        self.screenshot()
        seed_name = self.read_seed_name()
        if seed_name:
            logger.attr('种子名称', seed_name)
        else:
            logger.warning('未能识别种子名称')

        logger.info('点击确定种植')
        self.click(self.C_SEED_CONFIRM)
        self.device.click_record_clear()
        self.close_plant_success_popup()

    def count_empty_seed_slots(self) -> int:
        """统计 8 个种子格里空种子样式的数量"""
        image = self.device.image
        if image is None:
            return 0
        count = 0
        for roi in self.SEED_SLOT_ROIS:
            if self.match_in_roi(image, self.I_SEED_EMPTY_SLOT, roi):
                count += 1
        return count

    def first_seed_slot(self) -> tuple[int, int] | None:
        """返回第一个非空种子格的中心坐标"""
        image = self.device.image
        if image is None:
            return None
        for x, y, w, h in self.SEED_SLOT_ROIS:
            if self.match_in_roi(image, self.I_SEED_EMPTY_SLOT, (x, y, w, h)):
                continue
            return x + w // 2, y + h // 2
        return None

    @staticmethod
    def match_in_roi(image, rule, roi, threshold: float = None) -> bool:
        """在指定 roi 内匹配模板"""
        if image is None or rule.image is None:
            return False
        x, y, w, h = roi
        source = image[y:y + h, x:x + w]
        template = rule.image
        if source.size == 0 or template.size == 0:
            return False
        if template.shape[0] > source.shape[0] or template.shape[1] > source.shape[1]:
            return False
        threshold = rule.threshold if threshold is None else threshold
        result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
        return float(result.max()) >= threshold

    def read_seed_name(self) -> str:
        """读取点击种子后提示框里的种子名称"""
        image = self.device.image
        if image is None:
            return ''
        for item in self.O_SEED_NAME.detect_and_ocr(image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text or re.fullmatch(r'\d+', text):
                continue
            if '选择' in text or text in ('取消', '确定'):
                continue
            return text
        return ''

    def notify_no_seed(self) -> None:
        """无种子时发送通知"""
        logger.error('种植无种子，请补充种子')
        self.config.notifier.push(title='种植ZhongZhi',
                                  content=f'<{self.config.config_name}> 种植无种子，请补充种子')

    # ---------------------------------------------------------------- 弹窗关闭
    def close_plant_success_popup(self) -> bool:
        """关闭「收获成功」完成弹窗，直到弹窗消失"""
        timer = Timer(self.SUCCESS_POPUP_TIMEOUT).start()
        seen = False
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_PLANT_SUCCESS):
                seen = True
                logger.info('关闭种植完成弹窗')
                self.appear_then_click(self.I_PLANT_POPUP_CLOSE)
                self.device.click_record_clear()
                self.device.sleep(0.6)
                continue
            if seen:
                return True
            self.device.sleep(0.2)
        if seen:
            logger.warning('种植完成弹窗未完全关闭')
        return seen

    def close_plant_popup(self) -> bool:
        """关闭「没有可种植新的土地」弹窗"""
        timer = Timer(self.PLANT_RESPONSE_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_PLANT_UNAVAILABLE):
                return True
            self.appear_then_click(self.I_PLANT_POPUP_CLOSE)
            self.device.click_record_clear()
            self.device.sleep(0.5)
        return False

    # ---------------------------------------------------------------- 土地倒计时
    def land_cooldown_target(self) -> datetime:
        """读取土地倒计时并返回下次运行时间"""
        self.screenshot()
        seconds = self.read_land_cooldown_seconds()
        if seconds is None:
            logger.warning(f'土地倒计时识别失败，按默认 {self.DEFAULT_COOLDOWN_SECONDS}s 处理')
            seconds = self.DEFAULT_COOLDOWN_SECONDS
        return datetime.now() + timedelta(seconds=seconds + self.COOLDOWN_MARGIN_SECONDS)

    def read_land_cooldown_seconds(self) -> int | None:
        """
        读取种植页面上的土地倒计时，返回最短的剩余秒数

        倒计时是「时'分'秒」，OCR 对原尺寸识别不稳定，放大 2x/3x 各试一次。
        """
        image = self.device.image
        if image is None:
            return None
        x, y, w, h = self.O_LAND_COOLDOWN.roi
        crop = image[y:y + h, x:x + w]
        if crop.size == 0:
            return None

        for scale in (2, 3):
            resized = cv2.resize(crop, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_CUBIC)
            candidates = []
            for item in self.O_LAND_COOLDOWN.model.detect_and_ocr(resized):
                candidates.extend(self.parse_cooldown_text(item.ocr_text))
            if candidates:
                seconds = min(candidates)
                logger.attr('土地倒计时', f'{seconds}s')
                return seconds
        return None

    @classmethod
    def parse_cooldown_text(cls, text: str) -> list[int]:
        """从 OCR 文本中解析时'分'秒，返回可能的秒数列表"""
        if not text:
            return []
        results = []
        for hours, minutes, seconds in cls.COOLDOWN_PATTERN.findall(text):
            results.append(int(hours) * 3600 + int(minutes) * 60 + int(seconds))
        return results


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        t = ScriptTask(config, device)
        t.run()
    except TaskEnd:
        pass
