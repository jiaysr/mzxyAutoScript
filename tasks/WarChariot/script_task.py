# This Python file uses the following encoding: utf-8
"""
参与战车（血盟战车 / 仙盟战车）：

1. 每天 start_time（默认 20:00）开放，提前 advance_time（默认 5 分钟）开始准备：
   游戏不在线就启动并登录，在线就切回主页面，保证开放时游戏在线且停留在主页面
2. 到点后每 check_interval 秒（默认 2s）检测一次文字区域，
   识别到 keyword（默认「仙盟战车」）后进入参与流程（见 join_chariot）
3. 参与流程：在按钮区域里寻找「接受」按钮图片并点击，然后确认当前地图已变成战车地图
   （准备地图「战车」/ 战斗地图「血盟战车」都算）；不是战车地图就重新识别文字重试，
   超过 enter_timeout 秒（默认 20s）直接报错
4. 进入战车地图后：等左下角准备阶段提示
   - 「准备阶段完成，开始战斗」：切到战斗地图后**先移动到敌方刷新点**
     （43,138 与 45,21 的另一端；开局两队各在出生点，这时没目标可锁）→ 到位后锁定可攻击
     目标并按战斗预设攻击；没锁到就连续点「目标」最多 10 次，仍没锁到就在两个刷新点之间
     来回移动找目标，移动中锁到目标立即停下开打；离开战车地图立即结束。
   - 「战车数量不足，无法开战」：本局轮空（对面没车队），停在准备地图直接弹胜利结算，
     点「确定」关掉后回到战车以外的地图，继续参与下一局。
   出图后读取结算弹窗：
   - 失败（I_CHARIOT_FAIL）：点「确定」关闭弹窗后结束任务
   - 胜利（I_CHARIOT_WIN）：点「确定」关闭弹窗，继续轮询参与按钮，可重复参与
   人物在车内死亡会直接出图且不弹死亡框，队友还在战斗时结算弹窗会延迟出现，
   所以出图后持续轮询结算弹窗，不假设马上就有结果
5. 世界频道「系统战车战结束」提示（I_CHARIOT_FINISH）全程每帧轮询，命中即结束
6. 从开放时间起超过 check_timeout（默认 1800s = 20:30）统一结束，并排到第二天的准备时间
7. 20:00 前后首次检测期间阵亡：复活后继续检测，不重跑任务；进入战车地图后死亡不弹框，直接出图
"""
import difflib
from datetime import datetime, timedelta

from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd)
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main
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


class ChariotTargetFound(Exception):
    """移动途中锁到可攻击目标，用来中断 map_move_to"""


class ChariotLeftMap(Exception):
    """移动途中离开战车地图"""


class ChariotDeadline(Exception):
    """移动途中到达任务截止时间"""


class ScriptTask(GameUi, WarChariotAssets):
    # 战车战场两个刷新点，敌方在另一端
    CHARIOT_POINTS = ((43, 138), (45, 21))
    # 战车相关地图名：准备地图「战车」与战斗地图「血盟战车/仙盟战车」都含「战车」
    CHARIOT_MAP_KEY = '战车'
    # 战斗地图名后缀（血盟战车 / 仙盟战车），准备地图只有「战车」，不算战斗地图
    CHARIOT_BATTLE_MAP_SUFFIX = '盟战车'
    # 连续点「目标」按钮的次数，全都没锁到就移动找目标
    CHARIOT_LOCK_ATTEMPTS = 10
    # 攻击停止（目标丢失 / 变成交互）后，先点几次「目标」找附近的其它敌人
    CHARIOT_RELOCK_ATTEMPTS = 3
    # 移动途中主动点「目标」的最小间隔（秒）
    CHARIOT_MOVE_LOCK_INTERVAL = 2.0
    # 移动到达容差（坐标单位）
    CHARIOT_MOVE_TOLERANCE = 5
    # 单段移动超时（秒）
    CHARIOT_MOVE_TIMEOUT = 20
    # 等待「准备阶段完成，开始战斗」提示的最长时间（秒）
    CHARIOT_MOVE_PROMPT_TIMEOUT = 90
    # 等待轮空结算弹窗的最长时间（秒）
    CHARIOT_RESULT_TIMEOUT = 15
    # 识别到战车文字后，找到接受按钮并进入战车地图的超时时间（秒）
    enter_timeout = 20
    # 进入战车地图后的地点名
    chariot_map_name = '血盟战车'

    def battle_task_preset(self) -> str:
        """本任务选的战斗预设（配置 -> battle_config.battle_preset）"""
        return str(self.config.war_chariot.battle_config.battle_preset or '')

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

        result = self.chariot_loop(deadline)
        logger.attr('战车结果', result)

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
    def ocr_texts(self) -> list:
        """
        识别当前帧检测区域的文字
        """
        return [result.ocr_text
                for result in self.O_CHARIOT_TEXT.detect_and_ocr(self.device.image, logDisplay=False)]

    def text_appear(self) -> bool:
        """
        当前帧里是否出现关键字
        """
        keyword = self.config.war_chariot.war_chariot_config.keyword
        return any(text_match(text, keyword) for text in self.ocr_texts())

    def chariot_battle_map_name(self, name: str) -> bool:
        """
        地点名是否属于战车战斗地图（血盟战车 / 仙盟战车）

        准备地图只叫「战车」，不算战斗地图（轮空时停在准备地图）。
        """
        if not name:
            return False
        if name.endswith(self.CHARIOT_BATTLE_MAP_SUFFIX):
            return True
        return self.map_name_match(name, self.chariot_map_name)

    def in_chariot_map(self, image=None) -> bool:
        """
        当前地图是否为战车战斗地图（血盟战车 / 仙盟战车）

        :param image: 可选，传入当前帧则不再重复截图
        """
        location = self.map_current_location(image)
        if location is None:
            return False
        return self.chariot_battle_map_name(location[0])

    def chariot_map_entered(self, image=None) -> bool:
        """
        是否已进入战车相关地图：准备地图「战车」或战斗地图「仙盟战车」
        （轮空时停在准备地图，不会进战斗地图）
        :param image: 可选，传入当前帧则不再重复截图
        """
        location = self.map_current_location(image)
        if location is None:
            return False
        return self.map_name_match(location[0], self.CHARIOT_MAP_KEY)

    def chariot_loop(self, deadline: datetime) -> str:
        """
        完整战车流程循环：

        - 不在战车地图：先看结算弹窗（胜负都点确定；胜利继续、失败结束），再轮询参与按钮进图
        - 在战车地图：等可移动提示 -> 移动到敌方刷新点 -> 锁定可攻击目标并按预设战斗
        - 世界频道「系统战车战结束」每帧轮询，命中即结束
        - 到 deadline 统一结束

        :return: 'finish' / 'fail' / 'timeout'
        """
        logger.hr('战车战循环')
        cfg = self.config.war_chariot.war_chariot_config
        joined_once = False
        last_text_check = None

        while datetime.now() < deadline:
            self.screenshot()
            now = datetime.now()

            # 世界频道结束模板：每帧轮询
            if self.I_CHARIOT_FINISH.match(self.device.image):
                # 活动结束时如果还挂着结算弹窗，先点掉再收尾
                if self.I_CHARIOT_WIN.match(self.device.image):
                    self.close_result_popup(self.I_CHARIOT_WIN)
                elif self.I_CHARIOT_FAIL.match(self.device.image):
                    self.close_result_popup(self.I_CHARIOT_FAIL)
                logger.info('战车战结束：世界频道提示「系统战车战结束」')
                return 'finish'

            # 结算弹窗与参与按钮都不是每帧必查，按 check_interval 间隔识别。
            # 离开战车地图不等于马上有结算：人物死亡会直接出图（不弹死亡框），
            # 队友还在战斗时结算弹窗会晚一些出现，所以这里持续轮询，不设「出图 N 秒必弹」的假设。
            if last_text_check is None or (now - last_text_check).total_seconds() >= cfg.check_interval:
                last_text_check = now
                # 参与过一次之后才会出现结算弹窗，之前不用查
                if joined_once:
                    if self.I_CHARIOT_FAIL.match(self.device.image):
                        logger.info('战车战结束：失败')
                        self.close_result_popup(self.I_CHARIOT_FAIL)
                        return 'fail'
                    if self.I_CHARIOT_WIN.match(self.device.image):
                        logger.info('战车战胜利，点击确定关闭结算弹窗')
                        self.close_result_popup(self.I_CHARIOT_WIN)
                        last_text_check = None
                        continue
                if self.text_appear():
                    self.join_chariot()
                    joined_once = True
                    result = self.chariot_battle(deadline)
                    if result == 'fail':
                        logger.info('战车战结束：失败')
                        return 'fail'
                    last_text_check = None
                    continue

            self.reset_records()

        logger.warning(f'战车战超时（截止 {deadline.strftime("%H:%M:%S")}），结束')
        return 'timeout'

    def close_result_popup(self, rule) -> None:
        """
        模板匹配结算弹窗的「确定」按钮并点击，直到弹窗消失

        按钮位置可能有轻微偏移，I_CHARIOT_CONFIRM 的搜索区已在按钮四周各放宽 40px；
        点击坐标取模板实际匹配到的位置，不用固定坐标。

        :param rule: I_CHARIOT_WIN / I_CHARIOT_FAIL，用来判断弹窗是否还在
        """
        logger.info(f'关闭结算弹窗 {rule.name}')
        timer = Timer(3).start()
        while not timer.reached():
            self.screenshot()
            if not rule.match(self.device.image):
                logger.info('结算弹窗已关闭')
                return
            if self.appear_then_click(self.I_CHARIOT_CONFIRM):
                logger.info(f'点击确定 {self.I_CHARIOT_CONFIRM.roi_front}')
            self.device.sleep(0.3)
        logger.warning('结算弹窗未关闭')

    def chariot_result(self, image=None) -> str | None:
        """
        当前帧是否出现战车结算弹窗，命中就点「确定」关闭

        结算弹窗会盖住右上角地图名（「仙盟战车」只剩「战车」/「车」），
        所以等待/战斗循环里要先判结算，再判地图名。

        :param image: 传入当前帧则不重新截图
        :return: 'win' / 'fail'；未出现返回 None
        """
        image = image if image is not None else self.screenshot()
        if self.I_CHARIOT_FAIL.match(image):
            logger.info('战车战结算：失败')
            self.close_result_popup(self.I_CHARIOT_FAIL)
            return 'fail'
        if self.I_CHARIOT_WIN.match(image):
            logger.info('战车战结算：胜利')
            self.close_result_popup(self.I_CHARIOT_WIN)
            return 'win'
        return None

    # ---------------------------------------------------------------- 死亡
    def handle_death(self) -> None:
        """
        首次检测期间（20:00 前后）阵亡：复活后继续检测，不重跑任务

        覆盖全局默认的「复活 + 整任务重跑」：重跑会把任务排回准备阶段，可能错过
        战车开放。进入战车地图后死亡不会弹死亡框，而是直接离开地图；离开地图后
        会自动传送到安全地图，之后不会再死亡，所以只需要处理这一段。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        logger.warning('Character died before joining war chariot, revive and continue')
        self.revive()
        # 等复活/地图加载稳定，加载中点击会丢
        self.device.sleep(1)

    # ---------------------------------------------------------------- 车内战斗
    def wait_chariot_move(self, deadline: datetime) -> str:
        """
        进入战车地图后等待准备阶段结束的提示

        - 「准备阶段完成，开始战斗」：正常开打，可以移动
        - 「战车数量不足，无法开战」：本局轮空（对面没车队），会直接弹胜利结算
        提示在左下角聊天栏，会闪烁约 1.5s，所以按帧轮询（截图本身即节流）。

        :return: 'move' / 'bye' / 'win' / 'fail' / 'timeout'
        """
        logger.hr('等待准备阶段提示')
        timer = Timer(self.CHARIOT_MOVE_PROMPT_TIMEOUT).start()
        while not timer.reached() and datetime.now() < deadline:
            # 这个等待最长 90s，期间没有点击，必须自己清卡死记录（否则 60s 会被判成卡死）
            self.reset_records()
            self.screenshot()
            # 对面没队伍时可能刚「开始战斗」就结算，弹窗会盖住地图名，先处理结算
            result = self.chariot_result(self.device.image)
            if result:
                return result
            if self.I_CHARIOT_BYE.match(self.device.image):
                logger.info('提示「战车数量不足，无法开战」，本局轮空')
                return 'bye'
            if self.I_CHARIOT_MOVE.match(self.device.image):
                logger.info('提示「准备阶段完成，开始战斗」，可以移动')
                # 提示出现后等一小会儿，避免刚结束倒计时就拖动
                self.device.sleep(1)
                return 'move'
            self.device.sleep(0.2)
        logger.warning(f'{self.CHARIOT_MOVE_PROMPT_TIMEOUT}s 内没看到准备阶段提示')
        return 'timeout'

    def wait_chariot_battle_map(self, deadline: datetime) -> str:
        """
        提示「可以移动」之后，人物会从准备地图切到战斗地图（仙盟战车），等它切过去

        :return: 'map' 已切到战斗地图 / 'win' / 'fail' 先出现结算 / 'timeout'
        """
        timer = Timer(10).start()
        while not timer.reached() and datetime.now() < deadline:
            self.reset_records()
            self.screenshot()
            result = self.chariot_result(self.device.image)
            if result:
                return result
            if self.in_chariot_map(self.device.image):
                return 'map'
            self.device.sleep(0.5)
        logger.warning('没等到切换到战斗地图')
        return 'timeout'

    def wait_chariot_result(self, deadline: datetime) -> str | None:
        """
        轮空后等结算弹窗（胜利/失败），出现就点「确定」关闭

        :return: 'win' / 'fail' / 没等到返回 None
        """
        logger.hr('等待轮空结算弹窗')
        timer = Timer(self.CHARIOT_RESULT_TIMEOUT).start()
        while not timer.reached() and datetime.now() < deadline:
            self.reset_records()
            self.screenshot()
            result = self.chariot_result(self.device.image)
            if result:
                return result
            self.device.sleep(0.3)
        logger.warning('轮空后没等到结算弹窗')
        return None

    def chariot_battle(self, deadline: datetime) -> str | None:
        """
        战车地图内战斗

        1. 等「准备阶段完成，开始战斗」提示后开始行动
        2. 开局两队都停在各自出生点，这时没有目标可锁 —— 先移动到敌方刷新点
           （移动途中每 2s 主动点「目标」找敌人，锁到立刻停下开打）
        3. 已锁定可攻击目标：按战斗预设打，直到目标丢失；停止后先点「目标」
           检查附近有没有其它敌人，没有才继续移动/换点
        4. 没锁到：连续点「目标」最多 CHARIOT_LOCK_ATTEMPTS 次（会清点击记录，避免连点保护）
        5. 还是没锁到：在两个刷新点之间来回移动找目标
        6. 离开战车地图或到截止时间立即结束

        :return: 'win' / 'fail' 本局结算已出现（弹窗已关闭）；未结束返回 None
        """
        logger.hr('战车地图战斗')
        self.ui_close_menu()
        state = self.wait_chariot_move(deadline)

        if state in ('win', 'fail'):
            return state

        if state == 'bye':
            # 轮空：对面没有车队，不会进战斗地图，倒计时结束直接弹胜利结算
            return self.wait_chariot_result(deadline)

        if state == 'timeout':
            # 提示没抓到：不在战斗地图就按轮空处理，在战斗地图就继续打
            if not self.in_chariot_map(self.device.image):
                logger.warning('没有准备阶段提示且不在战斗地图，按轮空处理')
                return self.wait_chariot_result(deadline)
        else:
            state = self.wait_chariot_battle_map(deadline)
            if state in ('win', 'fail'):
                return state

        points = self.CHARIOT_POINTS
        location = self.map_current_location()
        if location is None:
            logger.warning('读不到当前坐标，默认先移动到 (45,21)')
            move_target = points[1]
        else:
            current = (location[1], location[2])
            logger.attr('战车坐标', current)
            # 敌方在另一端：选离当前点最远的刷新点
            move_target = max(points, key=lambda p: abs(p[0] - current[0]) + abs(p[1] - current[1]))
        logger.attr('移动目标', move_target)
        # 开局两队都停在各自出生点，这时没有目标可锁 —— 先走到敌方位置再开打
        reached = False

        while datetime.now() < deadline:
            self.reset_records()
            self.screenshot()
            # 活动整体结束的消息在地图里也会刷出来，命中就收工
            if self.I_CHARIOT_FINISH.match(self.device.image):
                logger.info('战车战结束：世界频道提示「系统战车战结束」')
                return None
            # 结算弹窗会盖住右上角地图名（仙盟战车 -> 战车/车），必须先判结算
            result = self.chariot_result(self.device.image)
            if result:
                return result
            if not self.in_chariot_map(self.device.image):
                logger.info('已离开战车地图，结束战斗')
                return None

            if self.ui_target_attackable(image=self.device.image):
                logger.info('锁定到可攻击目标，开始攻击')
                self.battle_queue_until(self.chariot_battle_stop_check(deadline))
                # 先确认是不是本局已结算（弹窗在场时点「目标」无效）
                result = self.chariot_result()
                if result:
                    return result
                if datetime.now() < deadline and self.in_chariot_map(self.device.image):
                    # 攻击停止（目标丢失 / 变成交互）后，先点「目标」看看附近还有没有其它敌人
                    logger.info(f'攻击停止，点「目标」检查附近敌人'
                                f'（最多 {self.CHARIOT_RELOCK_ATTEMPTS} 次）')
                    self.chariot_try_lock(attempts=self.CHARIOT_RELOCK_ATTEMPTS)
                continue

            if not reached:
                # 先移动到敌方位置（移动途中锁到目标就停下开打）
                logger.info(f'先移动到敌方位置 {move_target}')
                state = self.chariot_move_until_target(move_target[0], move_target[1], deadline)
                if state == 'target':
                    continue
                if state in ('left', 'deadline'):
                    return None
                reached = True
                continue

            # 到了敌方位置还是没目标：点「目标」按钮换目标（连点会触发框架保护，见 chariot_try_lock）
            if self.chariot_try_lock():
                continue

            if not self.in_chariot_map(self.device.image):
                logger.info('已离开战车地图，结束战斗')
                return None

            # 还是没有目标：换另一个点来回移动找目标
            logger.info('没锁到目标，换个点继续找')
            move_target = points[1] if move_target == points[0] else points[0]
            reached = False

    def chariot_battle_stop_check(self, deadline: datetime):
        """
        攻击循环的停止条件：目标丢失 / 结算弹窗出现 / 离开战车地图 / 到任务截止时间

        地图名 OCR 比较重，2s 判一次即可。
        """
        lost = self.ui_target_lost_check()
        state = {'probe': datetime.now()}

        def _check() -> bool:
            if datetime.now() >= deadline:
                logger.warning('Attack stopped: deadline reached')
                return True
            now = datetime.now()
            if (now - state['probe']).total_seconds() >= 2:
                state['probe'] = now
                if self.exist_image():
                    image = self.device.image
                    # 结算弹窗会盖住地图名，先看弹窗；这里不关闭，交给外层统一处理
                    if self.I_CHARIOT_WIN.match(image) or self.I_CHARIOT_FAIL.match(image):
                        logger.info('Attack stopped: settlement popup appeared')
                        return True
                    if not self.in_chariot_map(image):
                        logger.info('Attack stopped: left chariot map')
                        return True
            return lost()

        return _check

    def chariot_try_lock(self, attempts: int = None) -> bool:
        """
        连续点「目标」按钮切换目标，直到锁定到可攻击目标或次数用尽

        同一个按钮连点会被框架判成「Too many click for a button」直接抛异常
        （一个按钮点满 10 次就触发），所以每次点击前先清掉点击记录 ——
        和 ui_ensure_target_locked 的做法一致。
        """
        attempts = self.CHARIOT_LOCK_ATTEMPTS if attempts is None else attempts
        for _ in range(attempts):
            self.screenshot()
            if self.ui_target_attackable(image=self.device.image):
                return True
            # 清掉点击/卡死记录，避免连点同一个按钮触发框架的连点保护
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            if not self.ui_lock_target():
                logger.warning('没有找到「目标」按钮，停止切换目标')
                return False
            self.device.sleep(0.4)
        self.screenshot()
        return self.ui_target_attackable(image=self.device.image)

    def chariot_lock_during_move(self) -> bool:
        """
        移动途中点一次「目标」，看看附近有没有可攻击目标

        只点「目标」并校验动作按钮，锁到返回 True，由调用方停止移动开打。
        点击间隔由 I_TARGET_BUTTON 的 interval 控制，不会一路狂点。
        """
        if self.ui_target_attackable(image=self.device.image):
            return True
        if not self.appear_then_click(self.I_TARGET_BUTTON,
                                      action=self.C_TARGET_BUTTON_CLICK,
                                      interval=self.CHARIOT_MOVE_LOCK_INTERVAL):
            return False
        self.device.click_record_clear()
        # 点完等目标/右下角动作按钮刷新
        self.device.sleep(0.4)
        self.screenshot()
        return self.ui_target_attackable(image=self.device.image)

    def chariot_move_until_target(self, x: int, y: int, deadline: datetime) -> str:
        """
        向指定坐标移动，途中锁到可攻击目标立即停下

        移动途中每 CHARIOT_MOVE_LOCK_INTERVAL 秒主动点一次「目标」，避免没锁目标时
        从敌人旁边走过去（甲板/障碍挡住直线路径时尤其重要）。

        :return: 'target' 锁到目标 / 'arrived' 到达 / 'blocked' 走不动或超时
                 / 'left' 离开战车地图 / 'deadline' 到任务截止时间
        """
        def on_position(name, cx, cy):
            if datetime.now() >= deadline:
                raise ChariotDeadline
            if not self.chariot_battle_map_name(name):
                raise ChariotLeftMap
            # map_move_read_pos 刚截过图，直接复用当前帧
            if self.ui_target_attackable(image=self.device.image):
                raise ChariotTargetFound
            if self.chariot_lock_during_move():
                logger.info('移动中点「目标」锁定到可攻击目标')
                raise ChariotTargetFound

        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 1:
            return 'deadline'
        try:
            arrived = self.map_move_to(x, y,
                                       tolerance=self.CHARIOT_MOVE_TOLERANCE,
                                       timeout=min(self.CHARIOT_MOVE_TIMEOUT, remaining),
                                       ensure_main=False,
                                       calibrate=False,
                                       on_position=on_position)
        except ChariotTargetFound:
            logger.info('移动中发现可攻击目标，停止移动')
            return 'target'
        except ChariotLeftMap:
            logger.info('移动中离开战车地图，停止移动')
            return 'left'
        except ChariotDeadline:
            logger.warning('移动中到达任务截止时间')
            return 'deadline'
        return 'arrived' if arrived else 'blocked'

    def join_chariot(self) -> None:
        """
        识别到战车文字后的参与流程：

        1. 在按钮区域（I_CHARIOT_ACCEPT 的 roiBack）里寻找「接受」按钮并点击
        2. 检测当前地图是否已变成战车地图（准备地图「战车」/ 战斗地图「仙盟战车」）：
           是就结束参与流程，不是就重新识别战车文字走一遍上面的流程
        3. 超过 enter_timeout 秒还没进入战车地图直接报错
        """
        logger.hr('参与战车')
        timer = Timer(self.enter_timeout).start()
        while 1:
            self.screenshot()
            if self.text_appear() and self.appear_then_click(self.I_CHARIOT_ACCEPT, interval=1):
                logger.info(f'点击接受按钮 {self.I_CHARIOT_ACCEPT.roi_front}')
            if self.chariot_map_entered():
                logger.info('已进入战车地图')
                break
            if timer.reached():
                raise GameStuckError(f'{self.enter_timeout}s 内没有进入战车地图')
            self.device.sleep(1)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()

