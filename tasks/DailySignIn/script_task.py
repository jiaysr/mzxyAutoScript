# This Python file uses the following encoding: utf-8
"""
每日签到（DailySignIn）

任务框架：
- 每日签到：打开奖励弹窗-签到板，按「本月已累计签到次数」判断是否已签到；
  未签到则点击未签到物品，优先用签到次数 +1 判断成功，
  次数不可用时回退到「已签到物品边框」模板判断
- 修心：角色-详情 -> 修心，点击按钮直到今日次数用尽
- 生肖福利礼包：辅助-生肖 -> 商店，领取每日免费福利礼包
- 俸禄：仙盟-信息，按钮点亮（蓝色）就点，点完变灰说明今天已领
- VIP 每日礼包：主页面 VIP 入口 -> VIP 页 -> 每日福利，无 VIP 时跳过

执行顺序按勾选动态生成：先做主页面级（VIP/签到），再进一次角色面板，
按「修心 -> 生肖福利礼包 -> 俸禄」串行，中间不回主页面。
死亡复活/掉线重启后继续当前节点，不整任务重跑。
"""
import re
from datetime import datetime

import cv2
import numpy as np

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.DailySignIn.assets import DailySignInAssets
from tasks.GameUi.assets import GameUiAssets as G
from tasks.DailySignIn.page import (REWARD_TABS, RewardTabTarget,
                                    page_reward_sign_in, page_shengxiao,
                                    page_shengxiao_shop, page_vip,
                                    page_vip_welfare, page_xianmeng_info,
                                    page_xianmeng_member, page_xiuxin)
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main


class DailySignInResume(Exception):
    """角色死亡并已复活：当前任务节点需要重新执行"""
    pass


class ScriptTask(GameUi, DailySignInAssets):
    SCHEDULER_NAME = 'DailySignIn'
    # 签到板找不到未签到物品时最多向下滑几次
    SIGN_IN_SCROLL_MAX = 6
    # 6 次都没找到时，重新打开奖励面板再试的次数
    SIGN_IN_REOPEN_MAX = 1
    # 修心按钮最多点击次数
    XIUXIN_MAX_CLICKS = 10
    # 俸禄最多点击次数（每天只能领一次，多留几次应对点击没生效）
    SALARY_MAX_CLICKS = 3
    # 领取俸禄按钮「点亮」的饱和度分界
    # 点亮是蓝色按钮（实测 ≈220），已领取会变灰（实测 ≈15），取中间值
    SALARY_LIT_SATURATION = 120
    # 领取俸禄后的奖励弹窗关闭按钮：只用绿X/紫X
    # 仙盟面板右上角就是公共「返回」(I_CLOSE_RETURN)，走 try_close_unknown_page
    # 会把整个仙盟面板一起关掉
    SALARY_POPUP_CLOSE = (G.I_CLOSE_X_GREEN_SMALL, G.I_CLOSE_X_GREEN, G.I_CLOSE_X_PURPLE)

    def run(self) -> None:
        logger.hr('每日签到')
        cfg = self.config.daily_sign_in.daily_sign_in_config
        logger.attr('修心', cfg.xiuxin)
        logger.attr('领取VIP每日礼包', cfg.claim_vip_gift)
        logger.attr('领取俸禄', cfg.claim_salary)
        logger.attr('领取生肖福利礼包', cfg.claim_zodiac_gift)

        # 最少页面跳转的执行顺序：
        # 1. 主页面级：VIP、签到都从主页面开弹窗，做完各自回主页面
        # 2. 角色面板级：修心/生肖/俸禄共用角色面板，一次进入后按侧栏顺序串起来，
        #    中间不再回主页面
        steps = []
        if cfg.claim_vip_gift:
            steps.append(('领取VIP每日礼包', self.do_claim_vip_gift))
        steps.append(('每日签到', self.do_daily_sign_in))
        if cfg.xiuxin:
            steps.append(('修心', self.do_xiuxin))
        if cfg.claim_zodiac_gift:
            steps.append(('领取生肖福利礼包', self.do_claim_zodiac_gift))
        if cfg.claim_salary:
            steps.append(('领取俸禄', self.do_claim_salary))

        try:
            for name, step in steps:
                self.run_step(name, step)
        finally:
            try:
                # 所有节点结束后统一回主页面，避免挡住后续任务
                self.run_step('回主页面', self.ensure_back_to_main)
            finally:
                # 正常/异常都重排，避免 next_run 停在过去霸占调度
                self.set_next_run(task='DailySignIn', success=True, finish=True)
        raise TaskEnd('DailySignIn')

    def run_step(self, name: str, func):
        """
        执行一个任务节点；节点内死亡复活后重新执行当前节点

        死亡由 `handle_death_resume` 复活后抛 `DailySignInResume`，这里接住重试，
        不会把整个任务从头重跑。
        """
        self._current_step = name
        while True:
            try:
                return func()
            except DailySignInResume:
                logger.warning(f'角色死亡并复活，继续当前节点：{name}')
                self.ui_reset_current_page()
                self.device.sleep(1)

    def handle_death_resume(self) -> None:
        """死亡复活 / 掉线重启后：继续当前任务节点，不整任务重跑"""
        self.ui_reset_current_page()
        raise DailySignInResume

    # ---------------------------------------------------------------- 页面导航
    def appear_then_operate(self, target, interval=None, skip_first_screenshot=True):
        """页面引擎点击自定义的奖励弹窗 tab"""
        if isinstance(target, RewardTabTarget):
            return self.ui_reward_tab_click(target.name,
                                            skip_first_screenshot=skip_first_screenshot)
        return super().appear_then_operate(target, interval, skip_first_screenshot)

    def ui_reward_tab_find(self, name: str) -> tuple | None:
        """OCR 找奖励弹窗 tab 的文字中心坐标"""
        roi = self.O_REWARD_TAB_BAR.roi
        for item in self.O_REWARD_TAB_BAR.detect_and_ocr(self.device.image, logDisplay=False):
            if self.ocr_name_pick(item.ocr_text, REWARD_TABS) != name:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            return x, y
        return None

    def ui_reward_tab_visible(self) -> list[str]:
        """当前可见的奖励弹窗 tab 名，按屏幕从左到右排序"""
        items = []
        for item in self.O_REWARD_TAB_BAR.detect_and_ocr(self.device.image, logDisplay=False):
            name = self.ocr_name_pick(item.ocr_text, REWARD_TABS)
            if name:
                items.append((name, float(item.box[0][0])))
        items.sort(key=lambda item: item[1])
        return [name for name, _ in items]

    def ui_reward_tab_scroll(self, name: str) -> bool:
        """目标 tab 不在当前可见范围时，按方向滑一次"""
        if name not in REWARD_TABS:
            return False
        visible = self.ui_reward_tab_visible()
        if not visible:
            return False
        target = REWARD_TABS.index(name)
        indexes = [REWARD_TABS.index(v) for v in visible]
        if target < min(indexes):
            logger.info(f'Scroll reward tabs right to find {name}')
            self.swipe(self.S_REWARD_TAB_SCROLL_RIGHT)
        elif target > max(indexes):
            logger.info(f'Scroll reward tabs left to find {name}')
            self.swipe(self.S_REWARD_TAB_SCROLL_LEFT)
        else:
            return False
        self.device.click_record_clear()
        self.device.sleep(0.6)
        return True

    def ui_reward_tab_click(self, name: str, max_swipe: int = 4,
                            skip_first_screenshot: bool = False) -> bool:
        """点击奖励弹窗顶部 tab（OCR 定位，必要时左右滑动查找）"""
        for _ in range(max_swipe + 1):
            if not skip_first_screenshot:
                self.screenshot()
            skip_first_screenshot = False
            coord = self.ui_reward_tab_find(name)
            if coord:
                logger.info(f'Click reward tab [{name}] at {coord}')
                self.device.click(x=coord[0], y=coord[1],
                                  control_name=f'reward_tab_{name}')
                self.device.click_record_clear()
                self.device.sleep(0.3)
                return True
            if not self.ui_reward_tab_scroll(name):
                break
        logger.warning(f'Reward tab [{name}] not found')
        return False

    def ensure_back_to_main(self) -> None:
        """关闭奖励弹窗回主页面（失败只告警，不覆盖任务原始异常）"""
        try:
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=30):
                logger.warning('无法关闭弹窗回主页面')
        except DailySignInResume:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f'回主页面失败：{e}')

    # ---------------------------------------------------------------- 签到
    def read_sign_in_count(self, retry: int = 3) -> int | None:
        """
        读签到板上「本月已累计签到N次」

        :return: N；连续几次都读不到返回 None
        """
        for attempt in range(1, retry + 1):
            self.screenshot()
            text = self.O_REWARD_SIGN_IN_COUNT.ocr(self.device.image) or ''
            match = re.search(r'(\d+)', text)
            if match:
                count = int(match.group(1))
                logger.attr('本月已签到次数', count)
                return count
            logger.warning(f'签到次数识别失败（{attempt}/{retry}）: [{text}]')
            self.device.sleep(0.5)
        return None

    def find_sign_in_slot(self, rule, threshold: float = None):
        """
        在签到区域里从左到右、从上到下找模板

        :return: (中心x, 中心y, 匹配框(x,y,w,h))；找不到返回 None
        """
        image = self.device.image
        if image is None:
            return None
        x, y, w, h = rule.roi_back
        source = image[y:y + h, x:x + w]
        template = rule.image
        if source.size == 0 or template is None or template.size == 0:
            return None
        if template.shape[0] > source.shape[0] or template.shape[1] > source.shape[1]:
            return None

        threshold = rule.threshold if threshold is None else threshold
        result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        if len(xs) == 0:
            return None
        # 先按 y 后按 x 排序 = 从上到下、从左到右
        top, left = min(zip(ys.tolist(), xs.tolist()))
        th, tw = template.shape[:2]
        box = (x + left, y + top, tw, th)
        return box[0] + tw // 2, box[1] + th // 2, box

    @staticmethod
    def sign_in_slot_signed(image, box, signed_rule, pad: int = 8) -> bool:
        """点击后，检查点击的那个格子是否已经变成「已签到物品边框」"""
        if image is None:
            return False
        x, y, w, h = box
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1 = min(image.shape[1], x + w + pad)
        y1 = min(image.shape[0], y + h + pad)
        source = image[y0:y1, x0:x1]
        template = signed_rule.image
        if source.size == 0 or template is None:
            return False
        if template.shape[0] > source.shape[0] or template.shape[1] > source.shape[1]:
            return False
        result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
        return float(result.max()) >= signed_rule.threshold

    def sign_in_result(self, image, box, before_count, after_count) -> bool:
        """
        判断这次点击是否签到成功

        优先看签到次数：签到前后次数 +1 就算成功；
        次数不可用（读不到）时，回退到原来的「已签到物品边框」模板判断。
        """
        if before_count is not None and after_count is not None:
            return after_count == before_count + 1
        return self.sign_in_slot_signed(image, box, self.I_REWARD_SIGN_IN_SIGNED)

    def close_item_detail_popup(self, timeout: float = 5) -> bool:
        """关闭点击已签到物品后弹出的物品详情弹窗"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_REWARD_ITEM_DETAIL_CLOSE):
                logger.info('关闭物品详情弹窗')
                self.appear_then_click(self.I_REWARD_ITEM_DETAIL_CLOSE)
                self.device.click_record_clear()
                self.device.sleep(0.5)
                return True
            self.device.sleep(0.2)
        return False

    def find_unsigned_with_scroll(self, max_scroll: int = None):
        """
        在当前签到板视口找未签到物品；找不到就向下滑动再找

        :param max_scroll: 最多下滑次数，默认 SIGN_IN_SCROLL_MAX
        :return: find_sign_in_slot 的结果；滑完仍找不到返回 None
        """
        max_scroll = self.SIGN_IN_SCROLL_MAX if max_scroll is None else max_scroll
        for attempt in range(max_scroll + 1):
            self.reset_records()
            self.screenshot()
            slot = self.find_sign_in_slot(self.I_REWARD_SIGN_IN_UNSIGNED)
            if slot is not None:
                return slot
            if attempt >= max_scroll:
                break
            logger.info(f'签到区域没有未签到物品，向下滑动（{attempt + 1}/{max_scroll}）')
            self.swipe(self.S_REWARD_SIGN_IN_SCROLL_DOWN)
            self.device.click_record_clear()
            self.device.sleep(0.6)
        return None

    def click_sign_in_slot(self, slot, before_count: int = None) -> bool:
        """
        点击未签到物品并确认结果

        优先看签到次数是否 +1；次数读不到时回退到已签到边框模板判断。
        """
        x, y, box = slot
        logger.info(f'点击未签到物品 at ({x},{y})')
        self.device.click(x=x, y=y, control_name='reward_sign_in_slot')
        self.device.click_record_clear()

        timer = Timer(3).start()
        fallback_logged = False
        while not timer.reached():
            # read_sign_in_count 内部会截图，device.image 随后可直接复用
            after_count = self.read_sign_in_count(retry=1)
            if self.sign_in_result(self.device.image, box, before_count, after_count):
                logger.info(f'签到成功（次数 {before_count} -> {after_count}）')
                return True
            if before_count is None or after_count is None:
                if not fallback_logged:
                    logger.warning('签到次数不可用，改用已签到物品边框判断')
                    fallback_logged = True
            if self.appear(self.I_REWARD_ITEM_DETAIL_CLOSE):
                break
            self.device.sleep(0.2)

        logger.info('点击后未确认签到成功，按今天已签到处理')
        self.close_item_detail_popup()
        return False

    def open_sign_in_page(self) -> None:
        """从主页面打开奖励弹窗并切到签到板"""
        self.ui_reset_current_page()
        if not self.ui_goto(page_reward_sign_in, timeout=60):
            raise GameStuckError('Reward sign-in page does not appear')

    def sign_in_today(self, before_count: int = None) -> bool:
        """
        尝试签到：先在当前视口找，找不到就向下滑，最多 6 次；

        6 次都没找到就重新打开一次奖励面板再来一轮，
        两轮完整 6 次滑动都没有未签到物品，判断今天已经签到过。

        :param before_count: 点击前读到的本月已签到次数；读不到传 None，
                             点击后用边框模板兜底判断
        """
        logger.hr('尝试签到')
        slot = self.find_unsigned_with_scroll()
        if slot is not None:
            return self.click_sign_in_slot(slot, before_count)

        for reopen in range(1, self.SIGN_IN_REOPEN_MAX + 1):
            logger.warning(f'连续 {self.SIGN_IN_SCROLL_MAX} 次滑动没有未签到物品，'
                           f'重新打开奖励面板（{reopen}/{self.SIGN_IN_REOPEN_MAX}）')
            self.ensure_back_to_main()
            self.open_sign_in_page()
            slot = self.find_unsigned_with_scroll()
            if slot is not None:
                return self.click_sign_in_slot(slot, before_count)

        logger.info('两次完整滑动都没有未签到物品，判断今天已签到')
        return False

    # ---------------------------------------------------------------- 各步骤
    def do_daily_sign_in(self) -> None:
        """打开奖励弹窗，检查本月签到次数，必要时签到，然后关闭弹窗"""
        logger.hr('打开奖励-签到板')
        self.open_sign_in_page()
        logger.info('已打开奖励弹窗-签到板')

        today = datetime.now().day
        count = self.read_sign_in_count()
        logger.attr('今天日期', today)
        if count is not None and count >= today:
            logger.info(f'本月已累计签到 {count} 次，今天（{today} 号）已签到，跳过')
        else:
            if count is None:
                logger.warning('签到次数读取失败，直接尝试签到')
            else:
                logger.info(f'本月已累计签到 {count} 次 < 今天 {today} 号，尝试签到')
            self.sign_in_today(before_count=count)

        # 签到结束，关掉奖励弹窗，继续下一个勾选项
        self.ensure_back_to_main()

    def do_xiuxin(self) -> None:
        """进入角色-详情-修心，点击修心按钮直到出现「今日次数已用尽」"""
        logger.hr('修心')
        self.ui_reset_current_page()
        if not self.ui_goto(page_xiuxin, timeout=60):
            raise GameStuckError('Xiuxin page does not appear')
        logger.info('已进入修心页面')

        for attempt in range(1, self.XIUXIN_MAX_CLICKS + 1):
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_XIUXIN_FINISHED):
                logger.info('修心已完成（今日次数已用尽）')
                break
            logger.info(f'点击修心按钮（{attempt}/{self.XIUXIN_MAX_CLICKS}）')
            self.click(self.C_XIUXIN_BUTTON)
            self.device.click_record_clear()

            # 每次修心都会弹提示窗，用通用绿 X 关闭。
            # 不能走 try_close_unknown_page：它会先点右上角「返回」，把修心页关掉。
            popup_timer = Timer(2).start()
            while not popup_timer.reached():
                self.screenshot()
                if self.appear(self.I_CLOSE_X_GREEN):
                    logger.info('关闭修心提示弹窗')
                    self.appear_then_click(self.I_CLOSE_X_GREEN)
                    self.device.click_record_clear()
                    break
                self.device.sleep(0.2)
            self.device.sleep(0.5)
        else:
            logger.info(f'已点击 {self.XIUXIN_MAX_CLICKS} 次，判定修心完成')
        # 留在修心页，下一节点从当前页面继续导航，避免先回主页面再绕回来

    def do_claim_vip_gift(self) -> None:
        """
        主页面入口进 VIP 页面，再进每日福利领取 VIP 每日礼包

        - 当前无特权等级（无 VIP）直接跳过；
        - 打开每日福利页不会关闭 VIP 页，关闭福利页后先回 VIP 页再回主页面。
        """
        logger.hr('领取VIP每日礼包')
        self.ui_reset_current_page()
        if not self.ui_goto(page_vip, timeout=60):
            raise GameStuckError('VIP page does not appear')

        self.screenshot()
        if self.appear(self.I_VIP_NONE):
            logger.info('当前无VIP特权等级，跳过VIP每日礼包')
            self.ensure_back_to_main()
            return

        if not self.ui_goto(page_vip_welfare, timeout=30):
            raise GameStuckError('VIP welfare page does not appear')
        logger.info('已打开VIP每日福利页面')

        self.screenshot()
        if self.appear(self.I_VIP_WELFARE_CLAIMED):
            logger.info('VIP每日礼包已领取，跳过')
        elif self.appear(self.I_VIP_WELFARE_CLAIMABLE):
            logger.info('点击立刻领取VIP每日礼包')
            self.appear_then_click(self.I_VIP_WELFARE_CLAIMABLE)
            self.device.click_record_clear()
            if self.wait_until_appear(self.I_VIP_WELFARE_SUCCESS, wait_time=10):
                logger.info('VIP每日礼包领取成功')
                self.appear_then_click(self.I_VIP_WELFARE_SUCCESS_CLOSE)
                self.device.click_record_clear()
                self.device.sleep(0.5)
            else:
                logger.warning('未出现VIP每日礼包领取成功弹窗')
        else:
            logger.info('未找到VIP每日礼包领取按钮，跳过')

        # 先关闭每日福利页回 VIP 页，再关闭 VIP 页回主页面
        if not self.ui_goto(page_vip, timeout=30):
            logger.warning('无法关闭VIP每日福利页面')
        self.ensure_back_to_main()

    def salary_saturation(self) -> float | None:
        """读「领取俸禄」按钮的平均饱和度；没有画面时返回 None"""
        image = self.device.image
        if image is None:
            return None
        x, y, w, h = self.C_CLAIM_SALARY.roi_front
        crop = image[y:y + h, x:x + w]
        if crop.size == 0:
            return None
        return float(cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)[:, :, 1].mean())

    def salary_button_lit(self) -> bool:
        """
        俸禄按钮是否还亮着（= 今天还没领）

        不能拿 `I_SALARY_UNAVAILABLE` 反着判断：那张模板只是灰掉的文字，
        TM_CCOEFF_NORMED 对亮度/对比度不敏感，按钮点亮（蓝色）时同样命中
        0.955（实测），会一直被当成「已领取」而跳过。
        这里改看颜色：点亮是蓝色（饱和度 ≈220），已领取是灰色（≈15）。
        """
        saturation = self.salary_saturation()
        if saturation is None:
            logger.warning('读不到画面，无法判断俸禄按钮状态')
            return False
        logger.attr('俸禄按钮饱和度', round(saturation, 1))
        return saturation >= self.SALARY_LIT_SATURATION

    def close_salary_popup(self) -> bool:
        """关掉领取俸禄后的奖励弹窗（只点绿X/紫X，绝不点「返回」）"""
        for close in self.SALARY_POPUP_CLOSE:
            if self.appear_then_click(close, interval=1.5):
                logger.info(f'关闭领取俸禄弹窗（{close.name}）')
                self.device.click_record_clear()
                self.device.sleep(0.5)
                return True
        return False

    def wait_salary_claimed(self, timeout: float = 4) -> bool:
        """点击后等按钮变灰（= 领取成功），期间顺手关掉奖励弹窗"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.salary_button_lit():
                return True
            if not self.close_salary_popup():
                self.device.sleep(0.3)
        return False

    def do_claim_salary(self) -> None:
        """
        仙盟-信息页领取俸禄

        俸禄每天只能领一次：按钮还亮着（蓝色）就点，点完等它变灰；
        变灰说明今天已领，直接结束。
        """
        logger.hr('领取俸禄')
        self.ui_reset_current_page()
        if not self.ui_goto(page_xianmeng_member, timeout=60):
            raise GameStuckError('Xianmeng member page does not appear')

        # 未加入仙盟时所有顶部 tab 都是不可用样式，直接跳过俸禄
        self.screenshot()
        if self.appear(self.I_XIANMENG_NONE):
            logger.info('未加入仙盟，跳过领取俸禄')
            return

        if not self.ui_goto(page_xianmeng_info, timeout=30):
            raise GameStuckError('Xianmeng info page does not appear')
        logger.info('已进入仙盟-信息页')

        for attempt in range(1, self.SALARY_MAX_CLICKS + 1):
            self.reset_records()
            self.screenshot()
            if not self.appear(self.I_XIANMENG_INFO_PAGE):
                # 被弹窗挡住或面板被关掉：先试关弹窗，不行再重新进信息页
                if self.close_salary_popup():
                    logger.info('关闭挡在仙盟信息页上的弹窗')
                    continue
                logger.warning('已离开仙盟信息页，重新进入')
                self.ui_reset_current_page()
                if not self.ui_goto(page_xianmeng_info, timeout=60):
                    raise GameStuckError('Xianmeng info page does not appear')
                continue
            if not self.salary_button_lit():
                logger.info('俸禄已领取（按钮已变灰）')
                return
            logger.info(f'点击领取俸禄（{attempt}/{self.SALARY_MAX_CLICKS}）')
            self.click(self.C_CLAIM_SALARY)
            self.device.click_record_clear()
            self.device.sleep(0.8)
            if self.wait_salary_claimed():
                logger.info('俸禄领取成功（按钮已变灰）')
                return
            logger.warning('点击后俸禄按钮仍然亮着，重试')

        logger.info(f'已点击 {self.SALARY_MAX_CLICKS} 次俸禄仍未见变灰，停止领取')
        # 留在仙盟信息页，任务收尾统一回主页面

    def do_claim_zodiac_gift(self) -> None:
        """辅助-生肖页进生肖商店，领取每日免费福利礼包"""
        logger.hr('领取生肖福利礼包')
        self.ui_reset_current_page()
        if not self.ui_goto(page_shengxiao_shop, timeout=60):
            raise GameStuckError('Shengxiao shop does not appear')
        logger.info('已打开生肖商店')

        self.screenshot()
        if self.appear(self.I_ZODIAC_GIFT_CLAIMABLE):
            logger.info('点击领取生肖福利礼包')
            self.appear_then_click(self.I_ZODIAC_GIFT_CLAIMABLE)
            self.device.click_record_clear()
            if self.wait_until_appear(self.I_ZODIAC_GIFT_SUCCESS, wait_time=10):
                logger.info('领取生肖福利礼包成功')
                self.appear_then_click(self.I_ZODIAC_GIFT_SUCCESS_CLOSE)
                self.device.click_record_clear()
                self.device.sleep(0.5)
            else:
                logger.warning('未出现生肖福利礼包领取成功弹窗')
        elif self.appear(self.I_ZODIAC_GIFT_CLAIMED):
            logger.info('生肖福利礼包已领取，跳过')
        else:
            logger.info('未找到生肖福利礼包按钮，跳过')
        # 关闭生肖商店回生肖页，留在辅助模块供下一节点继续
        if not self.ui_goto(page_shengxiao, timeout=30):
            logger.warning('无法关闭生肖商店')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        t=ScriptTask(config, device)
        t.device.click(552, 593)
    except TaskEnd:
        pass
