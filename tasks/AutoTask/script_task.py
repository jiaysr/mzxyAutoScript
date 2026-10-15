# This Python file uses the following encoding: utf-8
"""
自动任务（AutoTask）：每日任务的一种

流程：
先去队伍页确保单人（有队伍就离开） -> 进任务特权页（过期则通知并失败结束）
-> 进任务页（有弹窗就点绿 X 关闭，有/无任务不影响后续） -> 离开任务页，
后续正常流程在任务页之外（待补充）。
"""
from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd, TaskFailed
from module.logger import logger
from tasks.AutoTask.assets import AutoTaskAssets
from tasks.AutoTask.page import page_bonus, page_privilege
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_task, page_team


class ScriptTask(GameUi, AutoTaskAssets):
    SCHEDULER_NAME = 'AutoTask'

    def run(self) -> None:
        logger.hr('自动任务')
        try:
            # 自动任务开始不能是组队状态，先去队伍页看看
            self.ensure_no_team()
            # 进任务特权页：右下角出现 激活 按钮说明特权过期，通知并失败结束
            self.check_privilege()
            # 进任务页：有弹窗（您当前没有任务）就点绿 X 关掉；
            # 有任务和没任务后续流程一样，都不在任务页里
            self.enter_task_page()
            self.close_empty_popup()
            # TODO: 正常流程（在任务页之外，待补充）
            logger.info('任务页就绪，后续流程待补充')
        finally:
            # 正常/异常都要重排（走标准排期：success_interval + server_update），
            # 避免 next_run 停在过去一直霸占调度
            self.set_next_run(task='AutoTask', success=True, finish=True)
        raise TaskEnd('AutoTask')

    def check_privilege(self, timeout: int = 60) -> None:
        """
        进任务特权页，检查右下角是否出现 激活 按钮

        出现 = 没有任务特权（过期/未激活）：发任务自己的通知并按任务失败
        异常结束（TaskFailed -> 调度记失败一次，脚本继续跑下一个任务；
        不存错误现场、不发框架通用通知、不重启游戏）。
        通知走配置的推送渠道（未启用推送时只记日志）。
        """
        logger.hr('Check privilege')
        self.ui_reset_current_page()
        if not self.ui_goto(page_privilege, timeout=timeout):
            raise GameStuckError('Privilege page does not appear')
        self.reset_records()
        self.screenshot()
        if self.appear(self.I_PRIVILEGE_EXPIRED):
            logger.error('任务特权已过期，请检查续费')
            self.config.notifier.push(title='自动任务AutoTask',
                                      content=f'<{self.config.config_name}> 任务特权过期，请检查')
            raise TaskFailed('任务特权已过期，请检查续费')
        logger.info('Privilege active, proceed')

    def enter_task_page(self, timeout: int = 60) -> None:
        """
        主页面 -> 角色面板 -> 任务页，靠 I_PAGE_TASK（顶部任务 tab 选中样式）确认到达
        """
        logger.hr('Enter task page')
        self.ui_reset_current_page()
        if not self.ui_goto(page_task, timeout=timeout):
            raise GameStuckError('Task page does not appear')

    def ensure_no_team(self, timeout: int = 60) -> None:
        """
        去队伍页确保单人状态：自动任务开始不能是组队状态

        - 有队伍（出现「离开队伍」按钮）：点它 -> 等「队伍已经解散」弹窗 -> 点绿 X
        - 没队伍（「组队后经验翻倍」弹窗）：点绿 X 关闭
        - 两者都没有：干净的无队伍状态，直接返回
        """
        logger.hr('Ensure no team')
        self.ui_reset_current_page()
        if not self.ui_goto(page_team, timeout=timeout):
            raise GameStuckError('Team page does not appear')
        timer = Timer(30).start()
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

    def close_empty_popup(self) -> bool:
        """
        进任务页后如果弹出「您当前没有任务」，点绿 X 关闭。
        有任务和没任务后续流程一样，这里只负责清弹窗。
        :return: 弹窗出现过返回 True，否则 False
        """
        self.reset_records()
        self.screenshot()
        if self.appear_then_click(self.I_TASK_EMPTY_CLOSE):
            logger.info('Empty task popup closed')
            self.device.click_record_clear()
            return True
        return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).ui_goto(page_bonus)
    except TaskEnd:
        pass
