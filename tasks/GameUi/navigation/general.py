# This Python file uses the following encoding: utf-8
"""
通用功能（GeneralFunctions）：

- 背包相关的通用操作，任务直接 `self.bag_xxx()` 调用，不要各自实现
- 后续其他通用功能（非背包）也放这里

目前实现：
- `bag_repair_equipment()` 修理装备：背包页 -> 随身功能 -> 修理装备 -> 确定 -> 关提示 -> 回主页面
  -> 全程模板匹配 + 点击规则，不走 OCR：
     `I_BAG_MENU_REPAIR` 判弹窗、`I_BAG_REPAIR_CONFIRM_TEXT` 判确认框、
     `I_BAG_REPAIR_SUCCESS` / `I_BAG_REPAIR_NOTHING` 判结果
  -> 点修理后也可能直接弹「您没有装备需要修理」（无确认框），同样视为成功
- `ensure_no_team()` 退出当前社交队伍：AutoTask 开跑前 / WildSnowman 结束与让路时共用
"""
from module.base.timer import Timer
from module.exception import GamePageUnknownError, GameStuckError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.pages.page import page_item_bag, page_main, page_team
from tasks.base_task import BaseTask


class GeneralFunctions(BaseTask, GameUiAssets):
    # 单步等待（弹窗/提示出现或消失）超时，秒
    BAG_REPAIR_TIMEOUT = 10
    # 点击后等 UI 过渡的时间，秒
    UI_SETTLE = 0.5
    # 退队点击循环超时（秒）
    LEAVE_TEAM_TIMEOUT = 30

    def ensure_no_team(self, timeout: int = 60) -> None:
        """
        去队伍页确保单人状态：没有队伍就返回，有队伍就退掉

        - 有队伍（出现「离开队伍」按钮）：点它 -> 等「队伍已经解散」弹窗 -> 点绿 X
        - 没队伍（「组队后经验翻倍」弹窗）：点绿 X 关闭
        - 两者都没有：干净的无队伍状态，直接返回

        :param timeout: 导航到队伍页的超时（退队点击循环固定 LEAVE_TEAM_TIMEOUT 秒）
        """
        logger.hr('Ensure no team')
        self.ui_reset_current_page()
        # 队伍页走引擎寻路：侧栏「队伍」由 ui_sidebar_click 滚动查找，
        # 到达由 page_team 的 I_PAGE_TEAM 模板确认（单 tab 的 OCR 校验有拼接兜底）
        if not self.ui_goto(page_team, timeout=timeout):
            raise GameStuckError('Team page does not appear')
        timer = Timer(getattr(self, 'LEAVE_TEAM_TIMEOUT', 30)).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear_then_click(self.I_TEAM_LEAVE):
                logger.info('In team, clicked leave')
                self.device.click_record_clear()
                self.device.sleep(1.0)
                continue
            if self.appear_then_click(self.I_TEAM_POPUP_CLOSE):
                logger.info('Team popup closed')
                self.device.click_record_clear()
                self.device.sleep(0.5)
                continue
            logger.info('No team, proceed')
            return
        raise GameStuckError('Cannot leave team')

    def bag_repair_equipment(self) -> bool:
        """
        修理全部装备；「无需修理」同样返回 True

        失败或异常都会尝试回到主页面，避免把背包页留给后续任务。
        """
        logger.hr('Repair equipment')
        try:
            if not self.ui_goto(page_item_bag, timeout=40):
                raise GameStuckError('Item bag page does not appear')
            self.device.sleep(self.UI_SETTLE)

            if not self.bag_open_general_menu():
                logger.warning('General function menu does not appear')
                return False

            result = self.bag_click_repair()
            if result is None:
                logger.warning('Repair dialog does not appear')
                return False
            if result == 'nothing':
                logger.info('No equipment needs repair')
                self.bag_close_repair_tip(self.I_BAG_REPAIR_NOTHING)
                return True

            self.click(self.C_BAG_REPAIR_CONFIRM, interval=1)
            if not self.bag_wait_appear(self.I_BAG_REPAIR_SUCCESS):
                logger.warning('Repair success tip does not appear')
                return False
            self.bag_close_repair_tip(self.I_BAG_REPAIR_SUCCESS)

            logger.info('Repair equipment finished')
            return True
        finally:
            self.bag_back_to_main()

    # ------------------------------------------------------------------ 步骤
    def bag_open_general_menu(self) -> bool:
        """
        背包底部点「随身功能」，等「修理装备」按钮出现（即弹窗已打开）

        弹窗已开着就不重复点，避免把它点关。
        """
        if self.appear(self.I_BAG_MENU_REPAIR):
            return True
        self.click(self.C_BAG_GENERAL_FUNCTION, interval=1)
        return self.bag_wait_appear(self.I_BAG_MENU_REPAIR)

    def bag_click_repair(self):
        """
        在随身功能弹窗里点「修理装备」，等确认弹窗或「无需修理」提示
        :return: 'confirm' = 弹确认框待点确定；'nothing' = 无需修理；None = 超时
        """
        logger.info('Click repair equipment')
        hit = self.bag_click_until(
            self.I_BAG_MENU_REPAIR,
            (self.I_BAG_REPAIR_CONFIRM_TEXT, self.I_BAG_REPAIR_NOTHING),
        )
        if hit is self.I_BAG_REPAIR_CONFIRM_TEXT:
            return 'confirm'
        if hit is self.I_BAG_REPAIR_NOTHING:
            return 'nothing'
        return None

    def bag_close_repair_tip(self, tip, timeout: int = None) -> bool:
        """
        点提示条右上角 X，直到指定提示消失

        :param tip: 用来判断提示是否还在的模板（I_BAG_REPAIR_SUCCESS / I_BAG_REPAIR_NOTHING）
        """
        timer = Timer(self._bag_timeout(timeout)).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if not self.appear(tip):
                logger.info('Repair tip closed')
                return True
            self.click(self.C_BAG_TIP_CLOSE, interval=0.5)
            self.device.sleep(self.UI_SETTLE)
        logger.warning('Repair tip still visible')
        return False

    def bag_back_to_main(self) -> None:
        """
        收尾回主页面；只吞导航类异常

        不能宽泛 except Exception：任务自定义的死亡异常（如 WorldBossDeath /
        XianfuDeath）从 screenshot 里抛上来时必须继续往上传播，否则调用方会
        以为流程正常，实际停在半路。
        """
        try:
            if not self.ui_goto(page_main, timeout=30):
                logger.warning('Cannot return to main page')
        except (GameStuckError, GamePageUnknownError) as e:
            logger.warning(f'Return to main page failed: {e}')

    # ------------------------------------------------------------------ 通用等待
    def bag_wait_appear(self, target, timeout: int = None) -> bool:
        """
        等某个模板出现（每轮只截一次图）
        """
        timer = Timer(self._bag_timeout(timeout)).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(target):
                return True
            self.device.sleep(self.UI_SETTLE)
        return False

    def bag_click_until(self, click, stops: tuple, timeout: int = None):
        """
        反复点 click，直到 stops 里任一模板出现

        :return: 命中的模板（RuleImage），超时返回 None
        """
        timer = Timer(self._bag_timeout(timeout)).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            for stop in stops:
                if self.appear(stop):
                    logger.info(f'{stop.name} appeared')
                    return stop
            self.appear_then_click(click, interval=1)
            self.device.sleep(self.UI_SETTLE)
        return None

    def _bag_timeout(self, timeout: int = None) -> int:
        return self.BAG_REPAIR_TIMEOUT if timeout is None else timeout

    if __name__ == '__main__':
        from module.config.config import Config
        from module.device.device import Device
        from tasks.GameUi.game_ui import GameUi

        c = Config('oas1')
        d = Device(c)
        game = GameUi(config=c, device=d)
        game.screenshot()                    # 先拿首帧，appear/ui_goto 都依赖它
        ok = game.bag_repair_equipment()
        print('repair ->', ok)