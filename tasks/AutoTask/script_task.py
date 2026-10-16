# This Python file uses the following encoding: utf-8
"""
自动任务（AutoTask）：每日任务的一种

流程（循环直到除循环任务外全满）：
队伍页确保单人 -> 特权页查过期 -> 读 10 任务选下一个（加量优先）
-> 加量页调卡 -> 特权页进详情点自动 -> 回主页按间隔查完成弹窗
-> 回特权页重读选下一个。
"""
from module.base.timer import Timer
from module.config.utils import convert_to_underscore
from module.exception import GameStuckError, TaskEnd, TaskFailed
from module.logger import logger
from tasks.AutoTask.assets import AutoTaskAssets
from tasks.AutoTask.config import BonusMultiplier
from tasks.AutoTask.page import page_bonus, page_privilege
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main, page_team, page_role_detail

import random
import re
from datetime import datetime, timedelta


class ScriptTask(GameUi, AutoTaskAssets):
    SCHEDULER_NAME = 'AutoTask'

    # 查看详情按钮列 x（查看按钮中心；行 y 由 OCR 行定位给出）
    DETAIL_CHECK_X = 768
    # 单个自动任务的等待超时（秒），超时按卡死处理；
    # 「全部自动」一次会跑完多个未完成任务，实际超时 = 该值 x 剩余任务数（见 execute_all_auto）
    AUTO_FINISH_TIMEOUT = 1800
    # 等待完成时，连续多少次「没检测到执行中文字」就回特权页复查进度；
    # 每次判断间隔 = running_check_interval（默认 30s），即连续 10 次约 10 个间隔
    AUTO_UNKNOWN_LIMIT = 10
    # 主页面点地面区弹出中断确认（x, y, w, h，点其中随机点）
    INTERRUPT_CLICK_REGION = (395, 221, 442, 395)
    # 让路后下次恢复运行的间隔（分钟）：到期重跑，若其他任务还没结束就继续让
    YIELD_RESUME_MINUTES = 10

    # 需要「按指定时间恢复运行」的时间点：让路中断 / 死亡重跑时设置，
    # run() 的 finally 优先用它排期（server=False，不被服务端刷新时间覆盖）
    _resume_at = None
    # 是否处于「回主页面挂机等自动任务完成」阶段：此时死亡复活后游戏会自动续跑自动任务，
    # 不需要重跑本任务（见 handle_death）
    _waiting_auto = False

    # 任务特权页-左侧任务列表几何（1280x720，真机实测）：
    # 可视区高 587，一屏 7 行；行 pitch 约 84（名/进度各一个文本框，同行 y 一致，
    # 分组半窗 PRIVILEGE_ROW_HALF 据此定）
    PRIVILEGE_ROW_HALF = 42
    # 滚完等列表停稳再识别
    PRIVILEGE_SCROLL_SLEEP = 1.0
    # 最多滚几屏（10 个任务一屏 7 个，滚 1 次全见；多留余量防漏）
    PRIVILEGE_MAX_SCROLL = 3

    # 任务名 -> 加量配置字段（只有这 6 个支持加量卡；循环任务永不参选）
    BONUS_FIELD = {
        '修真任务': 'xiuzhen_bonus',
        '金钱任务': 'jinqian_bonus',
        '技能任务': 'jineng_bonus',
        '经验任务': 'jingyan_bonus',
        '坐骑任务': 'zuoqi_bonus',
        '仙盟任务': 'xianmeng_bonus',
    }
    # 不参选的任务（一直在列表最后，无进度概念）
    SKIP_TASKS = ('循环任务',)
    # 全量任务名 -> 最大进度（硬编码：OCR 只读已做数，总数以此为准；
    # 顺带当白名单——弹窗等杂文本不在表里直接丢掉。游戏改总数时同步改这里）
    TASK_TOTALS = {
        '修真任务': 4,
        '金钱任务': 13,
        '技能任务': 9,
        '经验任务': 9,
        '游历任务': 5,
        '坐骑任务': 3,
        '仙盟任务': 6,
        '麒麟任务': 5,
        '玲珑每日': 6,
        '循环任务': None,
    }

    def run(self) -> None:
        logger.hr('自动任务')
        # 恢复运行时间点（让路中断 / 死亡重跑时设置，finally 里优先用它排期）
        self._resume_at = None
        try:
            # 自动任务开始不能是组队状态，先去队伍页看看
            self.ensure_no_team()
            # 进任务特权页：右下角出现 激活 按钮说明特权过期，通知并失败结束
            self.check_privilege()
            # 循环：读表 -> 选任务 -> 调加量卡 -> 进详情点自动 -> 回主页等完成；
            # 除循环任务外全满则本轮完成；同一任务进度连续3轮不动则失败结束
            # （自动跑了但进度没涨：防无限空转）
            stall_key, stall_count = None, 0
            while True:
                privilege_tasks = self.read_privilege_tasks()
                for name, (done, total) in privilege_tasks.items():
                    if done is None and total is None:
                        progress = '无进度'
                    elif done is None:
                        progress = f'?/{total}'
                    else:
                        progress = f'{done}/{total}'
                    logger.attr(f'任务[{name}]', progress)
                pick = self.pick_next_task(privilege_tasks)
                if pick is None:
                    logger.info('除循环任务外进度全满，自动任务完成')
                    raise TaskEnd('AutoTask')
                task_name, bonus = pick
                logger.attr('下一个任务', task_name)
                if bonus != BonusMultiplier.NONE:
                    logger.attr('任务加量', bonus.value)
                done, total = privilege_tasks[task_name]
                key = (task_name, done)
                stall_count = stall_count + 1 if key == stall_key else 0
                stall_key = key
                if stall_count >= 3:
                    logger.error(f'[{task_name}]连续3轮进度不动（{done}/{total}），请检查')
                    self.config.notifier.push(title='自动任务AutoTask',
                                              content=f'<{self.config.config_name}> [{task_name}]连续3轮进度不动，请检查')
                    raise TaskFailed(f'[{task_name}]连续3轮进度不动，请检查')
                if bonus == BonusMultiplier.NONE:
                    # 无加量任务：加量页关卡 -> 特权页点全部自动
                    # （全部自动会一次跑完所有未完成任务，超时按剩余任务数放大）
                    self.ensure_bonus_card(task_name, bonus, done, total)
                    self.execute_all_auto(task_count=self.count_pending_tasks(privilege_tasks))
                else:
                    self.ensure_bonus_card(task_name, bonus, done, total)
                    self.execute_task(task_name)
        finally:
            # 让路中断 / 死亡重跑设置了恢复时间就按它排期（server=False 不被服务端覆盖），
            # 否则走标准排期；都是为了避免 next_run 停在过去一直霸占调度
            if self._resume_at is not None:
                self.set_next_run(task='AutoTask', target=self._resume_at,
                                  success=None, finish=True, server=False)
            else:
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

    def group_privilege_rows(self) -> list:
        """
        对当前截图（self.device.image）的任务列表按 y 聚行
        :return: [(行中心y, [(x, 文本), ...]), ...]，均为全图绝对坐标
        """
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
        return grouped

    def row_y_for(self, task_name: str) -> int | None:
        """
        当前屏找任务行，返回行中心 y；找不到返回 None
        （行文本去掉进度后与任务名完全相等才算命中）
        """
        for cy, row in self.group_privilege_rows():
            line = ' '.join(text for _, text in sorted(row))
            name = re.sub(r'进度\s*[:：]?\s*\d+\s*/\s*\d+', '', line).strip() or line.strip()
            if name == task_name:
                return int(cy)
        return None

    def parse_privilege_tasks(self) -> dict:
        """
        识别当前屏的任务名 -> (已做, 总数)；没有进度文案的记 (None, 总数)

        同一行「任务名 + 进度」y 一致，按 y 聚行后再用正则拆；
        OCR 偶尔把同行拆成多个框，先按 x 拼行再匹配所以都兼容。
        :return: {任务名: (done, total)}
        """
        self.reset_records()
        self.screenshot()
        result = {}
        for _, row in self.group_privilege_rows():
            line = ' '.join(text for _, text in sorted(row))
            match_progress = re.search(r'进度\s*[:：]?\s*(\d+)\s*/\s*(\d+)', line)
            if match_progress:
                # 有进度：去掉进度部分，剩下就是任务名（名不一定以"任务"结尾，如玲珑每日）；
                # 名字必须在硬编码表里，否则当弹窗杂文本丢掉；总数以表为准
                name = re.sub(r'进度\s*[:：]?\s*\d+\s*/\s*\d+', '', line).strip()
                if name not in self.TASK_TOTALS:
                    continue
                total = self.TASK_TOTALS[name]
                result[name] = (int(match_progress.group(1)), total)
            else:
                # 无进度文案：整行就是任务名（如循环任务），同样过白名单
                name = line.strip()
                if name not in self.TASK_TOTALS:
                    continue
                result[name] = (None, self.TASK_TOTALS[name])
        return result

    def privilege_scroll_down(self) -> None:
        """
        任务列表往下滚一页（Swipe 规则 S_PRIVILEGE_SCROLL_DOWN，
        约 3 行，滚完等停稳再识别）

        起终点位置决定成败：要在进度文字与查看按钮之间的竖条上
        （真机实测该处快甩一次翻 3 行；文字区/右侧空白条快甩零位移）。
        滚不动时调 res/swipe.json 里 roiFront / roiBack 的位置与间距。
        """
        self.swipe(self.S_PRIVILEGE_SCROLL_DOWN)
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
        :return: {任务名: (done, total)}，无进度文案的 done 为 None
        """
        logger.hr('读取特权任务列表')
        # 每次重读都先回到特权页（上一轮执行完停在主页面，ui_current 是旧的，
        # 必须作废缓存重新识别，否则 ui_goto 直接返回 True 不导航）
        self.ui_reset_current_page()
        if not self.ui_goto(page_privilege, timeout=60):
            raise GameStuckError('Privilege page does not appear')
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

    def is_task_pending(self, name: str, done: int | None, total: int | None) -> bool:
        """
        该任务是否还没做完（循环任务 / 已达总数的任务不算）
        """
        if name in self.SKIP_TASKS:
            return False
        if total is not None and done is not None and done >= total:
            return False
        return True

    def count_pending_tasks(self, tasks: dict) -> int:
        """
        未完成任务数（「全部自动」会一次跑完这些任务，用它估算等待超时）
        """
        return sum(1 for name, (done, total) in tasks.items()
                   if self.is_task_pending(name, done, total))

    def pick_next_task(self, tasks: dict) -> tuple | None:
        """
        选下一个要执行的任务
        - 循环任务不参选；进度已满跳过；进度未知按未满处理（宁可多打一次）
        - 勾选了加量的未完成任务优先（列表顺序），其次其他未完成任务
        - 除循环任务外全满 -> None（自动任务完成）
        :param tasks: read_privilege_tasks 的结果 {任务名: (done, total)}
        :return: (任务名, BonusMultiplier) / None
        """
        cfg = self.config.auto_task.auto_task_config
        pending_bonus, pending_plain = [], []
        for name, (done, total) in tasks.items():
            if not self.is_task_pending(name, done, total):
                continue
            field = self.BONUS_FIELD.get(name)
            bonus = getattr(cfg, field, BonusMultiplier.NONE) if field else BonusMultiplier.NONE
            if bonus != BonusMultiplier.NONE:
                pending_bonus.append((name, bonus))
            else:
                pending_plain.append((name, bonus))
        for pick in pending_bonus + pending_plain:
            return pick
        return None

    def ensure_bonus_card(self, task_name: str, bonus: BonusMultiplier,
                           done: int | None, total: int | None,
                           timeout: int = 60) -> None:
        """
        进任务加量页，按选中任务的加量配置把卡调到位（不加量也要检查：
        有激活的就停掉）

        - 按钮文案即状态：左键（3倍卡）/右键（5倍卡）显示"停止加量"=已激活，
          显示"激活加量卡"=未激活；want=NONE 时两边都停掉（省卡）
        - 加量任务按剩余次数核卡（总数-已做，如修真 3 倍 0/4 要 >= 4 张）：
          拥有量不够时，看 continue_without_bonus 配置——勾选则不激活直接做，
          不勾选则通知加量卡不足并失败结束（同任务特权过期）
        - 点完按钮等"激活/停止加量成功"弹窗，点绿 X 关闭后再校验状态翻转；
          超时没翻转就失败结束（避免带着错误的加量状态去做任务）
        """
        logger.hr(f'Ensure bonus card {task_name} {bonus.value}')
        self.ui_reset_current_page()
        if not self.ui_goto(page_bonus, timeout=timeout):
            raise GameStuckError('Bonus page does not appear')
        counts = self.read_bonus_counts()
        logger.attr('加量卡拥有', f"3倍x{counts['3x']} 5倍x{counts['5x']}")
        if bonus == BonusMultiplier.NONE:
            self.set_bonus_side(is_left=True, active=False)
            self.set_bonus_side(is_left=False, active=False)
            logger.info(f'Bonus card state ok: want={bonus.value}')
            return
        key = '3x' if bonus == BonusMultiplier.X3 else '5x'
        need = (total or 0) - (done or 0)
        have = counts[key] or 0
        logger.attr(f'加量卡需求[{task_name}]', f'{key} 剩余{need}次 拥有{have}张')
        if have < need:
            if self.config.auto_task.auto_task_config.continue_without_bonus:
                logger.warning(f'加量卡不足（{key} 要{need}有{have}），按配置不激活直接做')
                self.set_bonus_side(is_left=True, active=False)
                self.set_bonus_side(is_left=False, active=False)
                return
            logger.error(f'加量卡不足（{key} 要{need}有{have}），请检查')
            self.config.notifier.push(title='自动任务AutoTask',
                                      content=f'<{self.config.config_name}> 加量卡不足（{task_name}要{key}{need}张），请检查')
            raise TaskFailed(f'加量卡不足（{task_name}要{key}{need}张），请检查')
        self.set_bonus_side(is_left=True, active=(bonus == BonusMultiplier.X3))
        self.set_bonus_side(is_left=False, active=(bonus == BonusMultiplier.X5))
        logger.info(f'Bonus card state ok: want={bonus.value}')

    def read_bonus_counts(self) -> dict:
        """
        读两边加量卡的拥有量（"拥有：N"）
        :return: {'3x': 数量/None, '5x': 数量/None}
        """
        self.reset_records()
        self.screenshot()
        result = {}
        for key, rule in (('3x', self.O_BONUS_3X_COUNT), ('5x', self.O_BONUS_5X_COUNT)):
            texts = [item.ocr_text for item in rule.detect_and_ocr(self.device.image, logDisplay=False)]
            count = None
            for text in texts:
                match = re.search(r'拥有\s*[:：]?\s*(\d+)', text)
                if match:
                    count = int(match.group(1))
                    break
            if count is None:
                logger.warning(f'Bonus {key} count unreadable: {texts}')
            result[key] = count
        return result

    def set_bonus_side(self, is_left: bool, active: bool,
                       timeout: int = 20) -> None:
        """
        把一侧加量卡调到指定状态（True=激活中，False=未激活）
        :param is_left: True=3倍卡（左），False=5倍卡（右）
        """
        side = '3x' if is_left else '5x'
        if is_left:
            stop_rule, activate_rule = self.I_BONUS_3X_STOP, self.I_BONUS_3X_ACTIVE
        else:
            stop_rule, activate_rule = self.I_BONUS_5X_STOP, self.I_BONUS_5X_ACTIVE
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            # 先清操作反馈弹窗（挡住按钮，也挡 OCR；关掉再看状态）
            if self.appear_then_click(self.I_BONUS_TOAST_CLOSE, interval=1):
                self.device.click_record_clear()
                self.device.sleep(0.5)
                continue
            is_active = self.appear(stop_rule)
            if is_active == active:
                logger.info(f'Bonus {side} already {"active" if active else "inactive"}')
                return
            target = activate_rule if active else stop_rule
            logger.info(f'Click bonus {side} button to {"activate" if active else "stop"}')
            self.appear_then_click(target)
            self.device.click_record_clear()
            self.device.sleep(1.0)
        logger.error(f'加量卡{side}切换失败，请检查')
        self.config.notifier.push(title='自动任务AutoTask',
                                  content=f'<{self.config.config_name}> 加量卡{side}切换失败，请检查')
        raise TaskFailed(f'加量卡{side}切换失败，请检查')

    def open_task_detail(self, task_name: str, timeout: int = 90) -> None:
        """
        在特权列表找到任务行，点该行「查看」进详情（出现自动按钮即算进）

        查看详情按钮长得一样，只能按行 y 点对应列（DETAIL_CHECK_X）；
        点中后列表变详情，不再滚动，只等自动按钮出现（点空了则行还在，重退点）。
        调用前不一定在特权页（上一轮停在主页面），先导航过去。
        """
        logger.hr(f'Open task detail [{task_name}]')
        self.ui_reset_current_page()
        if not self.ui_goto(page_privilege, timeout=timeout):
            raise GameStuckError('Privilege page does not appear')
        self.reset_privilege_scroll()
        timer = Timer(timeout).start()
        clicked = False
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_DETAIL_AUTO) and self.detail_title_match(task_name):
                logger.info(f'Task detail [{task_name}] opened and verified')
                return
            y = self.row_y_for(task_name)
            if y is None:
                if clicked:
                    # 点过之后行找不到了：可能正在切详情页，等一等不滚动
                    self.device.sleep(0.5)
                    continue
                self.privilege_scroll_down()
                continue
            if clicked:
                # 行还在 = 上次没点中，重退点
                logger.info(f'Row [{task_name}] still there, click again at y={y}')
            else:
                logger.info(f'Click 查看 for [{task_name}] at ({self.DETAIL_CHECK_X},{y})')
            self.device.click(x=self.DETAIL_CHECK_X, y=y, control_name='privilege_detail_check')
            self.device.click_record_clear()
            clicked = True
            self.device.sleep(1.0)
        raise GameStuckError(f'Task detail [{task_name}] does not open')

    def detail_title_match(self, task_name: str) -> bool:
        """
        详情页顶部黄色任务名是否与选中任务一致（自动按钮出现后的第二重确认，
        容忍 OCR 形近字误识）

        不一致说明点错了行：点返回回列表，调用方重找重进
        """
        texts = [item.ocr_text.strip() for item in
                 self.O_DETAIL_TITLE.detect_and_ocr(self.device.image, logDisplay=False)]
        for text in texts:
            if not text:
                continue
            if self.ocr_name_match(text, task_name):
                return True
        logger.warning(f'Detail title {texts} != [{task_name}], back to list')
        self.appear_then_click(self.I_DETAIL_BACK)
        self.device.click_record_clear()
        self.device.sleep(1.0)
        return False

    def click_auto_start(self, timeout: int = 30) -> None:
        """
        点详情里的自动按钮；可能弹加量提示弹窗（关掉就行）；
        自动按钮消失即已启动（只剩返回按钮）
        """
        logger.hr('Click auto start')
        timer = Timer(timeout).start()
        clicked_at = None
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear_then_click(self.I_AUTO_POPUP_CLOSE, interval=1):
                logger.info('Auto popup closed')
                self.device.click_record_clear()
                self.device.sleep(0.5)
                continue
            if not self.appear(self.I_DETAIL_AUTO):
                if clicked_at is not None:
                    logger.info('Auto button gone, auto started')
                    return
                self.device.sleep(0.5)
                continue
            if clicked_at is None or clicked_at.reached():
                logger.info('Click auto button')
                self.appear_then_click(self.I_DETAIL_AUTO)
                self.device.click_record_clear()
                clicked_at = Timer(3).start()
            self.device.sleep(0.5)
        raise GameStuckError('自动按钮点不开')

    def detail_back_to_main(self, timeout: int = 30) -> None:
        """点右上角返回到主页面"""
        logger.hr('Back to main')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.ui_page_appear(page_main):
                logger.info('Back on main page')
                self.ui_reset_current_page()
                return
            self.appear_then_click(self.I_CLOSE_RETURN, interval=1)
            self.device.click_record_clear()
            self.device.sleep(0.5)
        raise GameStuckError('Back to main failed')

    def auto_finished(self) -> bool:
        """完成弹窗出现（任务已全部完成）即 True"""
        texts = [item.ocr_text for item in
                 self.O_AUTO_FINISH.detect_and_ocr(self.device.image, logDisplay=False)]
        return any('任务已全部完成' in text for text in texts)

    def auto_running(self) -> bool:
        """执行中文字出现（自动任务中）即 True"""
        texts = [item.ocr_text for item in
                 self.O_AUTO_RUNNING.detect_and_ocr(self.device.image, logDisplay=False)]
        return any('自动任务中' in text for text in texts)

    def other_task_due_soon(self) -> bool:
        """
        是否有其他任务快到点了（已到期，或一个检查间隔内到期）

        只在 non_blocking 开启时让路；优先级高于本任务的才看
        （AutoTask 排最后，实际就是看所有其他任务）
        """
        cfg = self.config.auto_task.auto_task_config
        if not cfg.non_blocking:
            return False
        window = timedelta(seconds=int(cfg.running_check_interval))
        now = datetime.now()
        for name in self.higher_priority_task_names():
            task = getattr(self.config.model, convert_to_underscore(name), None)
            scheduler = getattr(task, 'scheduler', None)
            if scheduler is None or not scheduler.enable:
                continue
            if scheduler.next_run <= now + window:
                logger.attr('让路任务', f'{name} {scheduler.next_run}')
                return True
        return False

    def interrupt_dialog_appear(self) -> bool:
        """是否中断自动任务确认弹窗出现"""
        texts = [item.ocr_text for item in
                 self.O_INTERRUPT_DIALOG.detect_and_ocr(self.device.image, logDisplay=False)]
        return any('是否中断自动任务' in text for text in texts)

    def interrupt_for_others(self) -> str:
        """
        让路中断：主页面点地面区 -> 确认弹窗点确定
        :return: 'yielded'（已中断，调用方排恢复时间后结束）
                 'finished'（点地面时完成弹窗已出，关掉按完成处理）
        """
        logger.hr('让路中断自动任务')
        x0, y0, w, h = self.INTERRUPT_CLICK_REGION
        x = random.randint(x0, x0 + w)
        y = random.randint(y0, y0 + h)
        logger.info(f'Click ground at ({x},{y}) to interrupt')
        self.device.click(x=x, y=y, control_name='auto_interrupt')
        self.device.click_record_clear()
        timer = Timer(15).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.interrupt_dialog_appear():
                logger.info('Interrupt dialog appeared, confirm')
                self.appear_then_click(self.I_INTERRUPT_CONFIRM)
                self.device.click_record_clear()
                self.device.sleep(1.0)
                return 'yielded'
            if self.auto_finished():
                logger.info('Finish popup appeared during yield, close it')
                self.appear_then_click(self.I_FINISH_POPUP_CLOSE)
                self.device.click_record_clear()
                self.device.sleep(0.5)
                return 'finished'
            self.device.sleep(0.5)
        raise GameStuckError('中断确认弹窗未出现')

    def wait_auto_finish(self, timeout: int = None) -> str:
        """
        回主页面挂机后，每隔 running_check_interval 秒截图判断一次：
        - 出现「任务已全部完成」弹窗 -> 关掉返回 'finished'
        - 出现「自动任务中」-> 继续等（并可让路）
        - 连续 AUTO_UNKNOWN_LIMIT 次都没检测到「自动任务中」
          （可能自动任务静默结束/界面异常）-> 先重启游戏恢复，
          再返回 'unknown'，由调用方回特权页复查进度、尝试继续执行，不再空等到超时

        :param timeout: 等待超时（秒），默认单个任务的 AUTO_FINISH_TIMEOUT；
                        「全部自动」会传入 AUTO_FINISH_TIMEOUT x 剩余任务数
        :return: 'finished'（完成弹窗出现并已关闭）/
                 'unknown'（已重启游戏，回特权页复查进度）
        """
        logger.hr('Wait auto finish')
        timeout = timeout if timeout is not None else self.AUTO_FINISH_TIMEOUT
        logger.attr('等待超时', f'{timeout}s')
        interval = int(self.config.auto_task.auto_task_config.running_check_interval)
        timer = Timer(timeout).start()
        unknown = 0
        # 标记进入挂机等待阶段：这期间死亡复活后游戏会自动续跑，不重跑本任务
        self._waiting_auto = True
        try:
            while not timer.reached():
                self.reset_records()
                self.screenshot()
                if self.auto_finished():
                    logger.info('Auto task finished, close popup')
                    self.appear_then_click(self.I_FINISH_POPUP_CLOSE)
                    self.device.click_record_clear()
                    self.device.sleep(0.5)
                    return 'finished'
                if self.auto_running():
                    unknown = 0
                    if self.other_task_due_soon():
                        result = self.interrupt_for_others()
                        if result == 'finished':
                            return 'finished'
                        self._resume_at = datetime.now() + timedelta(minutes=self.YIELD_RESUME_MINUTES)
                        logger.attr('让路恢复时间', self._resume_at.strftime('%Y-%m-%d %H:%M:%S'))
                        raise TaskEnd('AutoTask')
                else:
                    # 没检测到「自动任务中」：累计次数，够 AUTO_UNKNOWN_LIMIT 次就
                    # 重启游戏恢复，再由调用方回特权页复查进度
                    unknown += 1
                    if unknown >= self.AUTO_UNKNOWN_LIMIT:
                        logger.warning(f'连续 {unknown} 次未检测到「自动任务中」，重启游戏后回特权页复查进度')
                        self.restart_game_for_recover()
                        return 'unknown'
                self.device.sleep(interval)
            raise GameStuckError('自动任务执行超时')
        finally:
            self._waiting_auto = False

    def restart_game_for_recover(self) -> None:
        """
        长时间等不到「自动任务中」时的恢复手段：重启游戏

        此时界面/自动任务状态已经不正常，继续挂机也等不到完成弹窗；
        重启（登录流程复用重启任务）后回到主页面，页面缓存随之作废，
        由调用方回特权页复查进度、继续执行下一个任务
        """
        logger.hr('重启游戏恢复')
        self.ui_restart_game()
        logger.info('Game restarted, back to main page')

    def handle_death(self) -> None:
        """
        死亡处理：

        - 回主页面挂机等自动任务时（wait_auto_finish 阶段）死亡：复活后游戏的自动任务
          会自己继续，不需要重跑本任务，返回继续等待即可（AutoTask 特有的特殊处理）；
        - 其他阶段死亡：走框架默认逻辑（rerun_task -> 立即重跑本任务，
          run() finally 里的标准排期会被 _rerun_requested 忽略，不会被推到下次）。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        logger.warning('Character died')
        self.revive()
        if self._waiting_auto:
            logger.info('Died while waiting auto task, game auto continues')
            return
        self.rerun_task()

    def execute_all_auto(self, task_count: int = 1, timeout: int = 90) -> None:
        """
        一键自动（无加量任务时）：特权页点全部自动 -> 关可能弹出的加量提示
        -> 出现取消自动即已启动 -> 回主页等完成

        「全部自动」一次会把所有未完成任务跑完，所以等待超时按剩余任务数放大
        （单个任务 AUTO_FINISH_TIMEOUT，见 wait_auto_finish）。

        :param task_count: 本次「全部自动」预计要跑的任务数（用于估算等待超时）
        """
        logger.hr('Execute all auto')
        self.ui_reset_current_page()
        if not self.ui_goto(page_privilege, timeout=timeout):
            raise GameStuckError('Privilege page does not appear')
        timer = Timer(timeout).start()
        clicked_at = None
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear_then_click(self.I_AUTO_POPUP_CLOSE, interval=1):
                logger.info('Auto popup closed')
                self.device.click_record_clear()
                self.device.sleep(0.5)
                continue
            if self.appear(self.I_PRIVILEGE_CANCEL_AUTO):
                logger.info('All auto started')
                break
            if clicked_at is None or clicked_at.reached():
                logger.info('Click all auto button')
                self.appear_then_click(self.I_PRIVILEGE_AUTO_ALL)
                self.device.click_record_clear()
                clicked_at = Timer(3).start()
            self.device.sleep(0.5)
        else:
            raise GameStuckError('全部自动点不开')
        self.detail_back_to_main()
        result = self.wait_auto_finish(timeout=self.AUTO_FINISH_TIMEOUT * max(1, task_count))
        if result == 'finished':
            logger.attr('一键自动完成', '')
        else:
            logger.warning('未检测到完成弹窗，回特权页复查进度后继续')

    def execute_task(self, task_name: str) -> None:
        """
        执行一个自动任务：进详情 -> 点自动（关可能弹出的提示）-> 回主页等完成
        完成弹窗关掉后返回，调用方重读列表选下一个
        """
        logger.hr(f'Execute task [{task_name}]')
        self.open_task_detail(task_name)
        self.click_auto_start()
        self.detail_back_to_main()
        result = self.wait_auto_finish()
        if result == 'finished':
            logger.attr('任务完成', task_name)
        else:
            logger.warning(f'[{task_name}] 未检测到完成弹窗，回特权页复查进度后继续')

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


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).read_privilege_tasks()
    except TaskEnd:
        pass
