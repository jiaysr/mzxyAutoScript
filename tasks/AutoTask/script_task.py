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
from tasks.GameUi.pages.page import page_task, page_team, page_role_detail

import re


class ScriptTask(GameUi, AutoTaskAssets):
    SCHEDULER_NAME = 'AutoTask'

    # 任务特权页-左侧任务列表几何（1280x720，真机实测）：
    # 可视区高 587，一屏 7 行；行 pitch 约 84（名/进度各一个文本框，同行 y 一致）
    PRIVILEGE_ROW_PITCH = 84
    PRIVILEGE_ROW_HALF = 42
    # 列表滚动拖动的 x：必须在右侧空白条（850）上，文字区（490）拖动列表不跟手
    # （真机对照探针：x=490 三种拖法逐像素零变化，x=850 一次 250px 正好翻 3 行）
    PRIVILEGE_SCROLL_X = 850
    # 一次滚 3 行（约 250px），滚完等停稳再识别
    PRIVILEGE_SCROLL_ROWS = 3
    PRIVILEGE_SCROLL_SLEEP = 1.0
    # 最多滚几屏（10 个任务一屏 7 个，滚 1 次全见；多留余量防漏）
    PRIVILEGE_MAX_SCROLL = 3

    def run(self) -> None:
        logger.hr('自动任务')
        try:
            # 自动任务开始不能是组队状态，先去队伍页看看
            self.ensure_no_team()
            # 进任务特权页：右下角出现 激活 按钮说明特权过期，通知并失败结束
            self.check_privilege()
            # 读全量任务名 + 进度（翻页合并，最后一个循环任务无进度）
            privilege_tasks = self.read_privilege_tasks()
            for name, (done, total) in privilege_tasks.items():
                logger.attr(f'任务[{name}]', f'{done}/{total}' if total is not None else '无进度')
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

    def parse_privilege_tasks(self) -> dict:
        """
        识别当前屏的任务名 -> (已做, 总数)；没有进度文案的记 (None, None)

        同一行「任务名 + 进度」y 一致，按 y 聚行后再用正则拆；
        OCR 偶尔把同行拆成多个框，先按 x 拼行再匹配所以都兼容。
        :return: {任务名: (done, total)}
        """
        self.reset_records()
        self.screenshot()
        roi_x, roi_y = self.O_PRIVILEGE_TASK_LIST.roi[0], self.O_PRIVILEGE_TASK_LIST.roi[1]
        cells = []
        for item in self.O_PRIVILEGE_TASK_LIST.detect_and_ocr(self.device.image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text or text == '查看':
                continue
            cx = sum(point[0] for point in item.box) / len(item.box) + roi_x
            cy = sum(point[1] for point in item.box) / len(item.box) + roi_y
            cells.append((cy, cx, text))
        cells.sort()
        grouped = []
        for cy, cx, text in cells:
            if grouped and abs(cy - grouped[-1][0]) <= self.PRIVILEGE_ROW_HALF:
                grouped[-1][1].append((cx, text))
            else:
                grouped.append([cy, [(cx, text)]])
        result = {}
        for _, row in grouped:
            line = ' '.join(text for _, text in sorted(row))
            match_progress = re.search(r'进度\s*[:：]?\s*(\d+)\s*/\s*(\d+)', line)
            if match_progress:
                # 有进度：去掉进度部分，剩下就是任务名（名不一定以"任务"结尾，如玲珑每日）
                name = re.sub(r'进度\s*[:：]?\s*\d+\s*/\s*\d+', '', line).strip()
                if not name:
                    continue
                result[name] = (int(match_progress.group(1)), int(match_progress.group(2)))
            else:
                # 无进度文案：整行就是任务名（如循环任务）
                name = line.strip()
                if not name:
                    continue
                result[name] = (None, None)
        return result

    def privilege_scroll_down(self) -> None:
        """
        任务列表往下滚 PRIVILEGE_SCROLL_ROWS 行（按住拖，一次到位）

        必须用 hold_drag 而不是分段 swipe：minitouch 的 swipe 一滑即抬，
        84px 一段的小甩根本带不动这个列表（实测 3 段滚完原地不动）；
        按住拖到底再抬手，列表才会真正翻页。
        """
        x0, y0, w, h = self.O_PRIVILEGE_TASK_LIST.roi
        start_y = y0 + h - 40
        end_y = start_y - self.PRIVILEGE_ROW_PITCH * self.PRIVILEGE_SCROLL_ROWS
        logger.info(f'Scroll privilege list: ({self.PRIVILEGE_SCROLL_X},{start_y})'
                    f' -> ({self.PRIVILEGE_SCROLL_X},{end_y})')
        self.device.hold_drag((self.PRIVILEGE_SCROLL_X, start_y),
                              (self.PRIVILEGE_SCROLL_X, end_y),
                              hold=0.4, control_name='privilege_list_scroll')
        self.device.click_record_clear()
        self.device.sleep(self.PRIVILEGE_SCROLL_SLEEP)

    def reset_privilege_scroll(self) -> None:
        """切到任务加量再切回特权，列表滚动回到顶部"""
        logger.info('Reset privilege list scroll via tab switch')
        self.ui_tab_click('任务加量', module='任务')
        self.ui_tab_click('任务特权', module='任务')

    def read_privilege_tasks(self) -> dict:
        """
        读全量任务 + 进度：先回顶再逐屏翻页，按任务名合并；
        滚出新名字就继续，一屏没新增（到底了）就停。
        :return: {任务名: (done, total)}，无进度的为 (None, None)
        """
        logger.hr('读取特权任务列表')
        self.reset_privilege_scroll()
        merged = {}
        for index in range(self.PRIVILEGE_MAX_SCROLL + 1):
            current = self.parse_privilege_tasks()
            new_names = [name for name in current if name not in merged]
            merged.update(current)
            logger.attr(f'第{index + 1}屏', f'{len(current)}行 新增{len(new_names)}行')
            if index > 0 and not new_names:
                break
            if index < self.PRIVILEGE_MAX_SCROLL:
                self.privilege_scroll_down()
        return merged

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
        self.enter_team_page(timeout=timeout)
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

    def enter_team_page(self, timeout: int = 60) -> None:
        """
        进队伍页（特殊处理，不走侧栏 tab 校验）

        队伍模块只有一个 tab，选中态大黄字会被 OCR 拆成「队」「伍」两个框，
        ui_sidebar_click 的 tab 校验永远过不了。所以这里手动点侧栏，
        用 I_PAGE_TEAM 模板匹配确认到达（弹窗不盖 tab 栏，不影响确认）。
        """
        logger.hr('Enter team page')
        self.reset_records()
        self.screenshot()
        if self.appear(self.I_PAGE_TEAM):
            logger.info('Already on team page')
            self.ui_current = page_team
            self.ui_current_module = '队伍'
            return
        if not self.ui_goto(page_role_detail, timeout=timeout):
            raise GameStuckError('Role detail page does not appear')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_PAGE_TEAM):
                logger.info('Team page arrived')
                self.ui_current = page_team
                self.ui_current_module = '队伍'
                return
            coord = self.ui_sidebar_find('队伍')
            if coord is None:
                if not self.ui_sidebar_scroll('队伍'):
                    raise GameStuckError('Team sidebar entry not found')
                continue
            logger.info(f'Click sidebar 队伍 at ({coord[0]:.0f},{coord[1]:.0f})')
            self.device.click(x=coord[0], y=coord[1], control_name='ui_sidebar_队伍')
            self.device.click_record_clear()
            self.device.sleep(1.0)
        raise GameStuckError('Team page does not appear')

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
        ScriptTask(config, device).read_privilege_tasks()
    except TaskEnd:
        pass
