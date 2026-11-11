# This Python file uses the following encoding: utf-8
"""
不周山（Buzhoushan）

当前进度：进页 -> 次数 -> 选层 -> 点塔 -> 确认 -> 进层弹窗/记录初始坐标 -> 清怪
          -> 连续2次锁不到目标时查「前往下一层」，没有就以初始点来回巡逻索敌
          -> 最后一层清完等结算弹窗（通关/阵亡）-> 点「返回不周山」回挑战页。
战斗细节：目标死了先原地补锁一次（battle_config.fast_relock），补不到或本层已清完
          就交回外层；结算弹窗出现时会中断正在空转的锁目标（见 battle_interrupted）。
后续：多轮挑战（剩余次数>0 再开一局）、扫荡/奖励。

层数地图：1 层在右下、越高越往上，单屏 5~6 座塔（第1/11/21/31/41...）。
一次滑动（S_NEW，约 275px）翻一页 = 50 层；滑到 110 后继续滑会进入上方空白，
所以定位逻辑以「OCR 是否出现目标层 / 是否出现 110 / 画面是否还有变化」为停止条件。

调度语义：
- challenge_count=0：一直挑战到剩余次数为 0
- challenge_count>0：打到配置次数；超过可挑战次数时按剩余次数执行
"""
import re
from datetime import datetime

import cn2an

from module.base.timer import Timer
from module.exception import TaskEnd, TaskFailed
from module.logger import logger
from tasks.Buzhoushan.assets import BuzhoushanAssets
from tasks.Buzhoushan.page import page_buzhoushan
from tasks.GameUi.game_ui import GameUi


class BuzhoushanTargetFound(Exception):
    """巡逻移动途中锁到目标，用来中断 map_move_to"""


class ScriptTask(GameUi, BuzhoushanAssets):
    name = '不周山'

    # 副本地图名（右上角地点文字，如「不周山233,114」）
    DUNGEON_NAME = '不周山'

    # 层数标签文本：「第N层」；地图塔是阿拉伯数字，小地图面板是中文数字（第十三层）
    LAYER_PATTERN = re.compile(r'第\s*([0-9]+|[零一二三四五六七八九十百]+)\s*层')
    # 地图最高层（该层出现后不再往上滑）
    MAX_LAYER = 110
    # 滑动后等画面稳定（minitouch 一滑即抬，地图有惯性）
    SCROLL_SETTLE = 1.2
    # 单次运行最多连打几层（防止异常情况下无限循环）
    MAX_FLOORS = 10

    def run(self) -> None:
        logger.hr('不周山')
        # 本层初始坐标：{层数: (x, y)}，锁不到敌人时用来来回巡逻
        self.floor_start: dict[int, tuple[int, int]] = {}
        self.current_floor: int | None = None
        try:
            if self.in_dungeon():
                # 角色还留在副本里（上次没打完/手动进出）：不再导航/选层，直接继续清
                logger.warning('已经在不周山副本内，跳过导航与选层，从当前层继续')
                self.run_floors()
            elif self.select_and_enter():
                self.run_floors()
        finally:
            # 正常/异常都重排，避免 next_run 停在过去霸占调度
            self.set_next_run(task='Buzhoushan', success=True, finish=True)
        raise TaskEnd('Buzhoushan')

    def select_and_enter(self) -> bool:
        """
        不周山页面上的进入流程：读次数 -> 复位地图 -> 找目标层 -> 点塔 -> 确认

        :return: 是否成功进入副本（次数为 0 / 没确认进入返回 False）
        """
        if not self.ui_goto(page_buzhoushan):
            raise TaskFailed('无法进入不周山页面')
        # 可能残留上次的「次数用完」提示，先关掉再操作
        self.handle_no_attempt()

        cfg = self.config.buzhoushan.buzhoushan_config
        count = self.get_challenge_count()
        planned = self.planned_count(count, cfg.challenge_count)
        start_floor = int(cfg.layer_range.value.split('-')[0])
        logger.attr('剩余挑战次数', count)
        logger.attr('配置挑战次数', cfg.challenge_count)
        logger.attr('本次计划挑战', planned)
        logger.attr('挑战层数', cfg.layer_range.value)

        if planned <= 0:
            # 次数为 0 或识别失败：不点塔，避免白跑一次
            logger.warning('今日剩余挑战次数为 0（或识别失败），本次跳过')
            return False

        # 地图滚动状态不可控：先点顶部 tab 复位，再滑到目标层并点击
        self.reset_layers_view()
        coord = self.locate_layer(start_floor)
        if coord is None:
            raise TaskFailed(f'找不到第{start_floor}层')
        self.click_layer(start_floor, coord)
        if not self.confirm_layer(start_floor):
            logger.warning('未确认进入挑战（可能今日次数已用完）')
            return False
        return True

    def run_floors(self) -> None:
        """逐层推进：进层（等弹窗、记录初始点）-> 清怪 -> 有下一层就继续"""
        prev = self.current_floor
        for _ in range(self.MAX_FLOORS):
            floor = self.after_enter_floor(prev_floor=prev)
            if floor is None:
                break
            result = self.battle_floor()
            logger.attr('本层结果', result)
            if result == 'settle':
                # 通关或阵亡：处理结算弹窗（回挑战页），本次结束
                outcome = self.handle_settlement(continue_run=True)
                logger.attr('结算结果', outcome)
                break
            if result != 'next':
                break
            prev = floor

    # ---------------------------------------------------------------- 次数
    def get_challenge_count(self, retry: int = 3) -> int | None:
        """
        识别左下角「今日剩余挑战次数：N」

        数字在 ROI 最右侧，OCR 通常把它单独切成一个框，也会把「今日」重复切出来，
        所以优先取「纯数字框里最右边那个」，整行一起识别时再退化成正则取末位数字。
        """
        for attempt in range(retry):
            self.screenshot()
            results = self.O_BUZHOUSHAN_COUNT.detect_and_ocr(self.device.image, logDisplay=False)
            if results:
                logger.attr('不周山次数OCR', ' | '.join(item.ocr_text for item in results))
            count = self.parse_count(results)
            if count is not None:
                return count
            logger.warning(f'剩余次数识别失败，重试 {attempt + 1}/{retry}')
            self.device.sleep(0.5)
        return None

    @staticmethod
    def parse_count(results) -> int | None:
        """
        从 OCR 结果里取次数，识别不到返回 None

        页面上必须出现「次数」：不在不周山页（或 OCR 完全跑偏）时不要瞎取数字，
        之前就因为背包页 OCR 出「D121000」把次数读成了 121000。
        """
        if not results:
            return None
        text = ''.join(item.ocr_text for item in results)
        if '次数' not in text:
            return None
        boxes = []
        for item in results:
            value = item.ocr_text.strip()
            if value.isdigit():
                boxes.append((float(item.box[0][0]), int(value)))
        if boxes:
            boxes.sort()
            count = boxes[-1][1]
        else:
            numbers = re.findall(r'\d+', text)
            if not numbers:
                return None
            count = int(numbers[-1])
        # 每日剩余次数只可能是很小的数，超出范围按识别失败处理
        if not 0 <= count <= 99:
            logger.warning(f'次数 {count} 不在合理范围，按识别失败处理')
            return None
        return count

    @staticmethod
    def planned_count(remain: int | None, configured: int) -> int:
        """
        本次实际挑战次数

        - 0：打到剩余次数为 0
        - >0：设置值超过剩余次数时，按剩余次数执行
        """
        if remain is None:
            return 0
        if configured == 0:
            return remain
        return min(configured, remain)

    # ---------------------------------------------------------------- 层数地图
    @classmethod
    def parse_floor(cls, text: str) -> int | None:
        """从文本里取层数，兼容「第13层」（地图塔）和「第十三层」（小地图面板）"""
        match = cls.LAYER_PATTERN.search(text)
        if not match:
            return None
        value = match.group(1)
        if value.isdigit():
            return int(value)
        try:
            return int(cn2an.cn2an(value, 'smart'))
        except Exception:  # noqa: BLE001
            logger.warning(f'层数转换失败: {text!r}')
            return None

    def reset_layers_view(self) -> bool:
        """
        重置层数地图的滚动位置

        地图滑上去之后不会自己回位，点一下顶部「不周山」tab 就回到初始视图（1~41 层）。
        点完等页面稳定，再交给 read_layers/locate_layer 重新识别。
        """
        logger.info('重置层数地图（点顶部「不周山」tab）')
        if not self.ui_tab_click('不周山', module='挑战', interval=1):
            logger.warning('点击「不周山」tab 失败，地图可能没有复位')
            return False
        self.device.sleep(0.6)
        return True

    def read_layers(self) -> dict[int, tuple[int, int]]:
        """
        识别当前画面可见的层数塔

        :return: {层数: 标签中心屏幕坐标}，识别不到返回空 dict
        """
        self.screenshot()
        roi = self.O_BUZHOUSHAN_LAYERS.roi
        layers = {}
        for item in self.O_BUZHOUSHAN_LAYERS.detect_and_ocr(self.device.image, logDisplay=False):
            floor = self.parse_floor(item.ocr_text)
            if floor is None:
                continue
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            layers[floor] = (x, y)
        if layers:
            logger.info(f'可见层数: {sorted(layers)}')
        return layers

    def scroll_layers(self, reverse: bool = False) -> None:
        """
        滑动层数区域一页（约 50 层）

        向上滑（默认）= 看更高层；reverse=True 往回滑 = 看更低层。
        起点必须落在 S_NEW 的 roiFront（y≈175~209），落在顶部标题条上不响应拖动。
        """
        x1, y1, x2, y2 = self.S_NEW.coord()
        if reverse:
            x1, y1, x2, y2 = x2, y2, x1, y1
        logger.info(f'Scroll layers {"down" if reverse else "up"} ({x1},{y1}) -> ({x2},{y2})')
        self.device.swipe(p1=(x1, y1), p2=(x2, y2), control_name='buzhoushan_layer_scroll')
        self.device.sleep(self.SCROLL_SETTLE)

    def locate_layer(self, floor: int, max_swipe: int = 3) -> tuple[int, int] | None:
        """
        滑到目标层并返回标签中心坐标（只定位，不点击）

        停止条件：目标层出现 / 已看到 110 层 / 滑动后可见层数没有变化。

        :param floor: 层数，如 51（对应配置区间 51-60）
        :param max_swipe: 最多向上滑几次
        :return: 标签中心坐标，找不到返回 None
        """
        layers = self.read_layers()
        for attempt in range(max_swipe + 1):
            if floor in layers:
                coord = layers[floor]
                logger.attr('目标层', f'第{floor}层 @ {coord}')
                return coord
            if self.MAX_LAYER in layers:
                logger.warning(f'已到顶，未找到第{floor}层')
                return None
            before = set(layers)
            self.scroll_layers()
            layers = self.read_layers()
            if not layers or set(layers) == before:
                logger.warning(f'滑动后画面没有变化，停止找第{floor}层')
                return None
            logger.info(f'第{attempt + 1}次滑动后可见: {sorted(layers)}')
        if floor in layers:
            return layers[floor]
        logger.warning(f'滑 {max_swipe} 次仍未找到第{floor}层')
        return None

    def click_layer(self, floor: int, coord: tuple[int, int] | None = None) -> bool:
        """
        点击目标层

        识别到的「第N层」标签中心就是游戏里的可点位置（选了 1-10 就点第1层）。

        :param floor: 层数，如 1
        :param coord: 已定位好的坐标，不传则先 locate_layer
        :return: 是否点击
        """
        coord = coord or self.locate_layer(floor)
        if not coord:
            logger.warning(f'没有第{floor}层的位置，跳过点击')
            return False
        logger.info(f'点击第{floor}层 @ {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name=f'buzhoushan_layer_{floor}')
        self.device.click_record_clear()
        return True

    def confirm_layer(self, start_floor: int, timeout: float = 6) -> bool:
        """
        处理点塔后的二次确认弹窗

        弹窗文案「要挑战不周山的第N - M层吗？」：先校验 N 与目标起始层一致，
        一致才点「确定」，不一致点「取消」退出（防止点到别的塔浪费次数）。

        :param start_floor: 期望的起始层，如 1
        :param timeout: 等待弹窗出现的超时
        :return: 是否已确认进入
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.handle_no_attempt():
                return False
            if not self.appear(self.I_BUZHOUSHAN_CONFIRM):
                self.device.sleep(0.3)
                continue
            text = ''.join(item.ocr_text
                           for item in self.O_BUZHOUSHAN_CONFIRM_TEXT.detect_and_ocr(self.device.image,
                                                                                     logDisplay=False))
            logger.attr('确认弹窗', text)
            match = re.search(r'第\s*(\d+)', text)
            if match and int(match.group(1)) != start_floor:
                logger.error(f'弹窗层数 {match.group(1)} 与目标 {start_floor} 不一致，点取消')
                self.click(self.C_BUZHOUSHAN_CONFIRM_CANCEL)
                self.device.click_record_clear()
                return False
            logger.info(f'确认挑战第{start_floor}层')
            self.click(self.C_BUZHOUSHAN_CONFIRM_OK)
            self.device.click_record_clear()
            return True
        logger.warning('没有等到挑战确认弹窗')
        return False

    def handle_no_attempt(self) -> bool:
        """
        处理「您今天挑战次数已经用完」提示弹窗

        次数为 0 时点塔会弹这个框，只有一个绿X；不关掉会挡住后续点击。

        :return: 是否出现过该弹窗（出现过并已关闭）
        """
        if not self.appear(self.I_BUZHOUSHAN_NO_ATTEMPT):
            return False
        logger.warning('今日挑战次数已经用完（弹窗提示），关闭后结束')
        self.appear_then_click(self.I_CLOSE_X_GREEN)
        self.device.click_record_clear()
        self.device.sleep(0.5)
        return True

    # ---------------------------------------------------------------- 小地图面板
    def open_map_panel(self, timeout: float = 6) -> bool:
        """
        点右上角小地图，打开小地图面板（右侧有「世界地图」按钮）

        地牢里的小地图在 1199,4 · 78×78，和主页面入口 C_MAP_MINIMAP_ENTRY 中心一致，直接复用。
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_MAP_MINIMAP_WORLD_MAP):
                return True
            self.click(self.C_MAP_MINIMAP_ENTRY, interval=1)
            self.device.sleep(0.3)
        logger.warning('小地图面板没有打开')
        return False

    def close_map_panel(self, timeout: float = 5) -> bool:
        """关掉小地图面板（点右上角绿X）"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_MAP_MINIMAP_WORLD_MAP):
                return True
            self.appear_then_click(self.I_MAP_POPUP_CLOSE, interval=1)
            self.device.sleep(0.3)
        logger.warning('小地图面板没有关闭')
        return False

    def map_panel_texts(self) -> list[tuple[str, int, int]]:
        """
        识别小地图面板右侧按钮列

        :return: [(文本, 屏幕x, 屏幕y), ...]；需要先 open_map_panel
        """
        roi = self.O_BUZHOUSHAN_MAP_PANEL.roi
        results = []
        for item in self.O_BUZHOUSHAN_MAP_PANEL.detect_and_ocr(self.device.image, logDisplay=False):
            box = item.box
            x = int((box[0][0] + box[1][0]) / 2) + roi[0]
            y = int((box[0][1] + box[2][1]) / 2) + roi[1]
            results.append((item.ocr_text, x, y))
        return results

    def map_current_floor(self) -> int | None:
        """读小地图面板上的当前层（第N层 / 第十三层）"""
        for text, _, _ in self.map_panel_texts():
            floor = self.parse_floor(text)
            if floor is not None:
                return floor
        return None

    def map_next_floor(self, click: bool = True) -> bool:
        """
        本层清完后进入下一层

        小地图面板里只有「本层没怪了」才会多出「前往下一层」按钮（位置随按钮数量上下浮动，
        所以按 OCR 到的坐标点，不写死）。没有这个按钮说明还没清完，关掉面板继续打。

        :param click: False = 只检查有没有下一层，不点（调试/暂停用），检查完关面板
        :return: 是否有「前往下一层」（click=True 时表示已点击）
        """
        if not self.open_map_panel():
            return False
        floor = self.map_current_floor()
        if floor is not None:
            logger.attr('当前层', floor)
        for text, x, y in self.map_panel_texts():
            if '前往下一层' not in text:
                continue
            if not click:
                logger.info('面板里有「前往下一层」（未点击）')
                self.close_map_panel()
                return True
            logger.info(f'点击「前往下一层」 @ ({x},{y})')
            self.device.click(x=x, y=y, control_name='buzhoushan_next_floor')
            self.device.click_record_clear()
            # 点完先把小地图面板关掉，再等进层弹窗（面板不关会挡住弹窗/后续识别）
            self.close_map_panel()
            return True
        logger.info('面板里没有「前往下一层」，本层还没清完')
        self.close_map_panel()
        return False

    # ---------------------------------------------------------------- 进层
    def handle_floor_popup(self, appear_timeout: float = 3, close_timeout: float = 4) -> int | None:
        """
        处理进层提示弹窗「不周山第N层」

        看到就主动点绿X关掉（不等它 3 秒自动消失），关掉后立刻进入战斗。

        :return: 弹窗里的层数；没看到弹窗返回 None
        """
        floor = None
        timer = Timer(appear_timeout).start()
        while not timer.reached():
            self.screenshot()
            text = ''.join(item.ocr_text for item in
                           self.O_BUZHOUSHAN_FLOOR_POPUP.detect_and_ocr(self.device.image, logDisplay=False))
            # 必须带「不周山」：小地图面板里也有「第十三层」这种浮字，光看「第N层」会误判
            if self.DUNGEON_NAME not in text:
                self.device.sleep(0.15)
                continue
            floor = self.parse_floor(text)
            if floor is not None:
                logger.attr('进层提示', text)
                break
            self.device.sleep(0.15)
        if floor is None:
            logger.info('没有看到进层提示弹窗')
            return None

        # 主动关闭：点弹窗右侧绿X，最多点 3 次
        logger.info(f'第{floor}层：点绿X关闭进层弹窗')
        for _ in range(3):
            self.appear_then_click(self.I_CLOSE_X_GREEN, interval=0.2)
            self.device.click_record_clear()
            self.device.sleep(0.15)
            self.screenshot()
            text = ''.join(item.ocr_text for item in
                           self.O_BUZHOUSHAN_FLOOR_POPUP.detect_and_ocr(self.device.image, logDisplay=False))
            if self.DUNGEON_NAME not in text:
                return floor
        logger.warning('绿X没关掉进层弹窗，等它自动消失')
        timer = Timer(close_timeout).start()
        while not timer.reached():
            self.device.sleep(0.2)
            self.screenshot()
            text = ''.join(item.ocr_text for item in
                           self.O_BUZHOUSHAN_FLOOR_POPUP.detect_and_ocr(self.device.image, logDisplay=False))
            if self.DUNGEON_NAME not in text or self.parse_floor(text) is None:
                return floor
        logger.warning('进层弹窗迟迟不消失')
        return floor

    def read_location(self):
        """读右上角地点+坐标（副本内是「不周山233,114」）"""
        return self.map_current_location()

    def in_dungeon(self) -> bool:
        """是否已经在不周山副本里（地点名匹配「不周山」）"""
        location = self.read_location()
        if not location:
            return False
        return self.map_name_match(location[0], self.DUNGEON_NAME)

    def after_enter_floor(self, prev_floor: int = None, settle: float = 4.0,
                          retry: int = 3) -> int | None:
        """
        进层后处理：等提示弹窗消失 -> 确定层数 -> 记录本层初始坐标

        「前往下一层」点完有加载，弹窗可能几秒后才出现；如果没抓到弹窗，
        就读小地图面板，并且要求读到的层数 **不等于进入前的层**（防止读到的还是旧画面），
        不满足就等一会儿重读，最多 retry 次。

        :param prev_floor: 进入前的层数，None 表示不校验（恢复场景）
        :param settle: 每次重读前等待的秒数（首次等久一点，之后缩短）
        :param retry: 最多重读几次
        """
        floor = self.handle_floor_popup(appear_timeout=6)
        if floor is not None and (prev_floor is None or floor != prev_floor):
            return self._record_floor(floor)

        for _ in range(retry):
            self.device.sleep(settle)
            if self.open_map_panel():
                current = self.map_current_floor()
                self.close_map_panel()
                if current is not None and (prev_floor is None or current != prev_floor):
                    return self._record_floor(current)
            settle = 2.0

        if floor is None:
            logger.warning('进层后确认不了当前层数')
            return None
        logger.warning(f'层数仍是 {floor}（可能刚加载完还没刷新），按当前层继续')
        return self._record_floor(floor)

    def _record_floor(self, floor: int) -> int:
        """记录本层初始坐标，返回层数"""
        location = self.read_location()
        if location is None:
            logger.warning(f'第{floor}层：读不到坐标，先不记录初始位置')
            return floor
        _, x, y = location
        self.current_floor = floor
        self.floor_start[floor] = (x, y)
        logger.attr('本层初始位置', f'第{floor}层 ({x},{y})')
        return floor

    # ---------------------------------------------------------------- 巡逻
    def current_floor_start(self) -> tuple[int, int] | None:
        """当前层进入时记录的初始坐标"""
        if self.current_floor is None:
            return None
        return self.floor_start.get(self.current_floor)

    def patrol_and_lock(self, start: tuple[int, int] = None, radius: int = 40,
                        rounds: int = 4, move_timeout: float = 8) -> bool:
        """
        锁不到敌人、也没有出现下一层时：以本层初始点为中心来回移动，
        移动途中持续尝试锁定目标（锁到就停下，交给战斗循环）。

        :param start: 初始坐标，不传则用本层记录的
        :param radius: 每次移动的偏移距离（坐标单位）
        :param rounds: 最多来回几趟
        :param move_timeout: 单段移动超时
        :return: 是否锁定到可攻击目标
        """
        start = start or self.current_floor_start()
        if start is None:
            logger.warning('没有本层初始位置，无法巡逻')
            return False
        for i in range(rounds):
            offset_x = radius if i % 2 == 0 else -radius
            offset_y = radius if (i // 2) % 2 else 0
            target = (start[0] + offset_x, start[1] + offset_y)
            logger.info(f'巡逻到 {target}（以 {start} 为中心）')
            try:
                self.map_move_to(target[0], target[1], ensure_main=False, calibrate=False,
                                 tolerance=6, timeout=move_timeout, on_position=self._patrol_probe)
            except BuzhoushanTargetFound:
                logger.info('巡逻途中锁到目标')
                return True
            if self.ui_target_attackable():
                return True
        logger.warning(f'巡逻 {rounds} 趟没有锁到目标')
        return False

    def _patrol_probe(self, name, x, y):
        """map_move_to 的每步回调：看到可攻击目标就中断移动"""
        if self.ui_target_attackable():
            raise BuzhoushanTargetFound

    # ---------------------------------------------------------------- 战斗
    def battle_task_preset(self) -> str:
        """本任务选的战斗预设（配置 -> battle_config.battle_preset）"""
        return str(self.config.buzhoushan.battle_config.battle_preset or '')

    def battle_floor(self, deadline: datetime = None, max_patrol: int = 3,
                     allow_next: bool = True) -> str:
        """
        清本层：击杀所有可攻击目标（不指定目标名称）

        循环：
        1. 已锁定可攻击目标 -> 按战斗预设一直打到目标丢失；
        2. 小地图出现黄色菱形传送点 -> 本层清完，开面板看有没有「前往下一层」；
        3. 没有目标 -> 点「目标」尝试锁定（每次最多点 2 次、2s 超时），锁不到累计一次失败；
        4. 连续 2 次锁不到（传送点没认出来时的兜底）-> 查面板；没有下一层就
           以本层初始点为中心来回巡逻，边移动边索敌。

        :param deadline: 截止时间，None 表示不限制
        :param max_patrol: 连续巡逻几次没收获就放弃（返回 'stuck'）
        :param allow_next: 清完后是否点「前往下一层」（调试时可关掉停在当前层）
        :return: 'next' 进入下一层 / 'cleared' 清完但没进下一层 / 'timeout' / 'stuck'
        """
        fail = 0
        patrol_miss = 0
        while True:
            if deadline is not None and datetime.now() >= deadline:
                logger.warning('清怪超时')
                return 'timeout'
            self.screenshot()
            # 通关/阵亡结算弹窗：出现就结束本层循环（阵亡在不周山里没有死亡弹窗，只有失败结算）
            if self.I_BUZHOUSHAN_SETTLE.match(self.device.image):
                logger.info('出现结算弹窗（通关或阵亡），结束本层循环')
                return 'settle'
            if self.ui_target_attackable(image=self.device.image):
                logger.info('锁定到可攻击目标，开始攻击')
                if self.battle_queue_attack() == 'settle':
                    return 'settle'
                fail = 0
                patrol_miss = 0
                continue

            # 小地图出现黄色菱形传送点 = 本层怪已经清完（首选判定）
            if self.has_teleport(image=self.device.image):
                logger.info('本层已清完（小地图出现传送点标记）')
                if not allow_next:
                    return 'cleared'
                if self.map_next_floor(click=True):
                    return 'next'
                # 最后一层没有「前往下一层」，等通关结算弹窗
                logger.info('没有「前往下一层」（可能是最后一层），等待结算弹窗')
                if self.wait_settlement(timeout=8):
                    return 'settle'
                logger.warning('清完后没等到结算弹窗，按清完结束')
                return 'finish'

            # 兜底：传送点没认出来时，连续 2 次锁不到目标也去查一下面板
            if fail >= 2:
                logger.warning('连续 2 次没锁到目标，检查有没有下一层')
                if self.map_next_floor(click=allow_next):
                    return 'next' if allow_next else 'cleared'
                if patrol_miss >= max_patrol:
                    logger.warning('巡逻多次仍没目标、也没有下一层，先结束本层')
                    return 'stuck'
                logger.warning('没有下一层，开始边移动边索敌')
                if self.patrol_and_lock():
                    fail = 0
                    continue
                patrol_miss += 1
                fail = 0
                continue

            # 已锁定但动作不是「攻击」（采集/交互物）时，多点一次「目标」切走；
            # 真空场时也就多花 0.5s，比误判成「清完」去巡逻划算
            # abort_check：结算弹窗已经把画面压暗时，锁目标只会空转到超时（实测 6s）
            if self.ui_ensure_target_locked(max_switch=2, timeout=2,
                                            abort_check=self.battle_interrupted):
                if self.battle_queue_attack() == 'settle':
                    return 'settle'
                fail = 0
                patrol_miss = 0
                continue

            fail += 1
            logger.info(f'没锁到目标（{fail}/2）')
            self.device.sleep(0.2)

    # ---------------------------------------------------------------- 清完检测
    def has_teleport(self, image=None) -> bool:
        """
        本层怪物是否已清完（右上角小地图出现黄色菱形传送点标记）

        标记位置随传送点在当前地图的位置变化，所以在小地图范围内搜索（roiBack）。
        注意：小地图上还有一种黄色的「!」任务感叹号，纯颜色检测会把两者混在一起，
        所以这里只用 7x8 的菱形模板做形状匹配（实测对「!」不命中）。

        :param image: 传入当前帧则不再重复截图
        """
        if image is None:
            self.screenshot()
            image = self.device.image
        return self.I_BUZHOUSHAN_TELEPORT.match(image)

    # ---------------------------------------------------------------- 结算
    def wait_settlement(self, timeout: float = 8) -> bool:
        """等结算弹窗出现（只看不点）"""
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.I_BUZHOUSHAN_SETTLE.match(self.device.image):
                return True
            self.device.sleep(0.3)
        return False

    def settle_result(self) -> str | None:
        """
        读结算弹窗结果

        :return: 'success'（恭喜您通关）/ 'fail'（很遗憾你尚未通关）/ 'unknown'
                 / None（没有结算弹窗）
        """
        self.screenshot()
        if not self.I_BUZHOUSHAN_SETTLE.match(self.device.image):
            return None
        text = ''.join(item.ocr_text for item in
                       self.O_BUZHOUSHAN_SETTLE_TITLE.detect_and_ocr(self.device.image, logDisplay=False))
        logger.attr('结算标题', text)
        if '恭喜' in text:
            return 'success'
        if '遗憾' in text or '未通关' in text:
            return 'fail'
        return 'unknown'

    def handle_settlement(self, continue_run: bool = True, timeout: float = 10) -> str | None:
        """
        等待并处理通关/失败结算弹窗

        不周山里角色阵亡不会弹死亡框，只会弹这个失败结算（很遗憾你尚未通关），
        所以这里同时承担「通关收尾」和「阵亡收尾」。

        :param continue_run: True=点「返回不周山」回挑战页（还要继续打）；
                             False=点右上角绿X关掉（不再继续）
        :return: 'success' / 'fail' / 'unknown' / None（没等到弹窗）
        """
        timer = Timer(timeout).start()
        result = None
        while not timer.reached():
            result = self.settle_result()
            if result is not None:
                break
            self.device.sleep(0.3)
        if result is None:
            logger.warning('没有等到结算弹窗')
            return None
        if continue_run:
            logger.info('点「返回不周山」回挑战页')
            self.click(self.C_BUZHOUSHAN_SETTLE_RETURN)
        else:
            logger.info('点绿X关闭结算弹窗')
            self.appear_then_click(self.I_CLOSE_X_GREEN)
        self.device.click_record_clear()
        self.device.sleep(0.5)
        return result

    def battle_queue_attack(self) -> str:
        """
        队列模式打到目标丢失（本任务统一的战斗模式）

        - 每次挑「优先级最高且冷却好」的技能释放，没进冷却会按配置重试（队列模式自带）；
        - 每次点击后校验目标，目标丢失返回 'target_lost'；
        - 目标死了先原地补锁（配置 battle_config.fast_relock，见 battle_relock_after_kill），
          补到就留在同一轮里接着打，省掉退回外层重锁的 ~0.6s/只；
        - 出现结算弹窗（通关/阵亡）返回 'settle'。
        - 连点次数 / 点击间隔取战斗预设里的 clicks / click_interval

        :return: 'target_lost' / 'settle' / 'timeout' / 'no_slot'
        """
        preset = self.battle_preset()
        slots = self.battle_plan_slots(preset=preset)
        if not slots:
            logger.warning('没有识别到可释放的技能位，先补一次普通攻击')
            self.battle_click_attack()
            self.device.sleep(0.5)
            return 'no_slot'
        return self.battle_skill_loop(
            slots,
            check_stop=self.battle_attack_stop_reason,
            target_check=lambda image: self.ui_target_attackable(image=image),
            include_attack=self.battle_use_normal_attack(),
            on_target_lost=self.battle_relock_after_kill,
        )

    def battle_attack_stop_reason(self, image=None) -> str | None:
        """队列模式的停止条件：结算弹窗（通关/阵亡）出现就停"""
        if image is None:
            image = self.device.image
        if image is not None and self.I_BUZHOUSHAN_SETTLE.match(image):
            logger.info('Attack stopped: settlement popup')
            return 'settle'
        return None

    # ---------------------------------------------------------------- 中断与补锁
    def battle_interrupted(self) -> bool:
        """
        战斗被打断（结算弹窗出现）：锁目标这类「等界面响应」的循环要尽快退出

        结算弹窗会把背景压暗，右上角菜单的收起/展开特征都匹配不上，
        `ui_close_menu` 不放弃就会一路空转满 6s 超时（2026-11-05 日志最后一段
        从 02:05:15 白等到 02:05:23 才认到结算）。

        **只看最新一帧、不截图**：它会被锁目标的循环每轮调一次，
        这里再截一张就等于自己制造空转。
        """
        image = self.device.image if self.exist_image() else None
        if image is None:
            return False
        return bool(self.I_BUZHOUSHAN_SETTLE.match(image))

    def battle_relock_after_kill(self) -> bool:
        """
        目标死了原地补锁（battle_skill_loop 的 on_target_lost 回调）

        补锁一次约 0.45s，完整重锁约 1.1s，所以默认开（battle_config.fast_relock）。
        但「这一波已经打完了」时补锁纯属空切，所以先用最新一帧（不额外截图）挡掉两种情况：

        - 已出结算弹窗：交给循环收尾，别补；
        - 本层已清完（小地图出传送点）：场上不会有怪了，直接去点「前往下一层」。

        而且只补 1 次：不周山一层就 4~5 只怪，一次没补到就交回外层走完整流程，
        空切 3 次（~1.4s/层 × 10 层）比省下的还多。

        :return: True = 已补锁到下一个目标，循环接着打；False = 交回外层
        """
        if not self.battle_fast_relock():
            return False
        image = self.device.image if self.exist_image() else None
        if image is not None:
            if self.I_BUZHOUSHAN_SETTLE.match(image):
                logger.info('出现结算弹窗，不再补锁，交给外层收尾')
                return False
            if self.has_teleport(image=image):
                logger.info('本层已清完，不再补锁，准备前往下一层')
                return False
        return self.battle_relock_target(max_switch=1)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
