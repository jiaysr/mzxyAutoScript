# This Python file uses the following encoding: utf-8
"""
仙府九重天（XianfuJiuchongtian）

按配置的「阶段」收集对应颜色的仙玉碎片：打各大关的第 N 小关，小关 N 大概率掉颜色 N 的碎片
（1=南极、2=北极、3=东极、4=西极）；打大关 M 只会掉「仙玉碎片M」
（如持国天=M3 只掉碎片三）。翻牌颜色随机，所以按缺失的号从大到小打：
缺 4 → 打大关4的小关N，反复打到拿到碎片4，再打大关3拿碎片3 …… 集齐后重新从大关4开始。

仙石：每半小时 +1、上限 40。达到 stone_threshold 才开始清仙石，循环打到仙石 < 4 为止，
然后按 (阈值 - 当前仙石) * 30 分钟排下一次运行。

流程：挑战-仙府九重天页 -> 选大关 -> 选小关 -> 开始挑战 -> 副本内走 210,158 打怪
      -> 翻牌领奖 -> 关面板，循环直到仙石不够。
"""
import re
from datetime import datetime, timedelta

import cv2

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_item_bag, page_role_detail
from tasks.XianfuJiuchongtian.assets import XianfuJiuchongtianAssets
from tasks.XianfuJiuchongtian.page import page_xianfu

NUMERALS = ('一', '二', '三', '四')
DAGUAN_NAMES = {1: '增长天', 2: '多闻天', 3: '持国天', 4: '广目天'}
XIAOGUAN_COLORS = {1: '南极', 2: '北极', 3: '东极', 4: '西极'}

STONE_MAX = 40          # 仙石上限
STONE_REGEN_MIN = 30    # 每半小时恢复 1 点
STONE_COST = 4          # 每次挑战消耗

MAP_NAME = '仙府九重天'
BATTLE_TIMEOUT = 150
BATTLE_GROUPS_MAX = 20


class ScriptTask(GameUi, XianfuJiuchongtianAssets):

    # 本次翻牌结果 (color, numeral)；每场战斗更新
    last_reward = None
    # 读背包最多扫多少屏（整页滑动，一页 4 行）
    BAG_SCAN_SCREENS = 12

    def run(self) -> None:
        stage, threshold, max_battles_cfg, coord_text, click_empty = self.load_config()
        self.BATTLE_CLICK_EMPTY_SKILLS = click_empty
        color = XIAOGUAN_COLORS[stage]
        logger.hr(f'仙府九重天 阶段{stage}({color}) 阈值{threshold} '
                  f'最多{max_battles_cfg or "不限"}局 点空技能位={click_empty}')

        self.enter_xianfu_page()
        stone = self.read_stone()
        logger.attr('剩余仙石', stone)
        if stone is None:
            logger.warning('读不到仙石，退出')
            raise TaskEnd('XianfuJiuchongtian')
        if stone < threshold:
            logger.info(f'仙石 {stone} < 阈值 {threshold}，先不开始')
            self.schedule_next_run(stone, threshold)
            raise TaskEnd('XianfuJiuchongtian')

        counts = self.bag_piece_counts(color)
        logger.attr(f'{color}仙玉碎片数量', counts)
        max_battles = stone // STONE_COST + 3
        if max_battles_cfg > 0:
            max_battles = min(max_battles, max_battles_cfg)
        battles = 0
        while stone is not None and stone >= STONE_COST and battles < max_battles:
            target = self.pick_target(counts)
            logger.info(f'目标：{DAGUAN_NAMES[target]}(大关{target}) 第{stage}小关，'
                        f'{color}仙玉碎片{NUMERALS[target - 1]} 当前 {counts.get(target, 0)}')
            if not self.run_one_battle(target, stage, coord_text):
                logger.warning('战斗流程失败，停止清仙石')
                break
            battles += 1
            if self.last_reward is not None:
                rc, rn = self.last_reward
                if rc == color:
                    idx = NUMERALS.index(rn) + 1
                    counts[idx] = counts.get(idx, 0) + 1
                    logger.info(f'拿到 {rc}仙玉碎片{rn}，数量更新 {counts}')
                else:
                    logger.info(f'拿到 {rc}仙玉碎片{rn}（不是需要的颜色），继续打')
            else:
                logger.warning('本次没拿到碎片（翻牌没成），按原数量继续')
            stone = self.read_stone()
            logger.attr('剩余仙石', stone)

        self.schedule_next_run(stone, threshold)
        logger.info('仙府九重天清仙石结束')
        raise TaskEnd('XianfuJiuchongtian')

    # ---------------------------------------------------------------- 配置
    def load_config(self):
        try:
            cfg = self.config.xianfu_jiuchongtian.xianfu_config
            return (int(cfg.stage), int(cfg.stone_threshold), int(cfg.max_battles),
                    cfg.target_coord, bool(cfg.click_empty_skills))
        except Exception as e:  # noqa: BLE001
            logger.warning(f'读取配置失败({e})，用默认值 阶段1/阈值40/不限局数')
            return 1, 40, 0, '210,158', False

    @staticmethod
    def parse_coord(text: str):
        match = re.search(r'(\d{1,4})\s*[,，]\s*(\d{1,4})', text or '')
        if not match:
            return 210, 158
        return int(match.group(1)), int(match.group(2))

    def schedule_next_run(self, stone, threshold) -> None:
        """按仙石恢复速度排下一次运行时间"""
        now = datetime.now()
        if stone is None:
            logger.warning('仙石读不到，5 分钟后再试一次')
            target = now + timedelta(minutes=5)
        elif stone >= STONE_MAX:
            target = now + timedelta(minutes=1)
        else:
            need = max(0, threshold - stone)
            target = now + timedelta(minutes=need * STONE_REGEN_MIN)
        logger.attr('下次运行', target)
        try:
            self.set_next_run(task='XianfuJiuchongtian', target=target,
                              success=None, finish=True, server=False)
        except Exception as e:  # noqa: BLE001
            logger.warning(f'排期失败: {e}')

    # ---------------------------------------------------------------- 读仙石
    def read_stone(self):
        self.screenshot()
        results = self.O_XIANFU_STONE.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(item.ocr_text for item in results)
        logger.attr('仙石OCR', text)
        match = re.search(r'(\d+)\s*[/／]\s*(\d+)', text)
        if match:
            return int(match.group(1))
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else None

    # ---------------------------------------------------------------- 背包
    # 物品详情面板的特征文案（面板打开会污染背包识别）
    BAG_PANEL_MARKS = ('立即使用', '全部使用', '永久丢弃', '设置快捷', '免费合成')

    def bag_region_text(self, region) -> str:
        """对指定区域做一次 OCR，返回按阅读顺序（先 y 后 x）拼接的文本"""
        rule = self.O_BAG_GRID
        old_roi = rule.roi
        try:
            rule.roi = region
            results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        finally:
            rule.roi = old_roi
        boxes = []
        for item in results:
            box = item.box
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            boxes.append((item.ocr_text.strip(), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        boxes.sort(key=lambda it: (round(it[2] / 20), it[1]))
        return ''.join(text for text, _, _ in boxes)

    # 背包物品格固定区域（1280x720，真机实测）：
    # 文字区 = 图标中心x + 57，宽 125、高 93；数量区 = 图标中心x + 22，文字区上边 + 55，35x30
    # 实测按给定尺寸会把「碎片一/二/三」的末字裁掉，这里各方向放宽一点（不会碰到隔壁格）
    BAG_COLUMN_CENTERS = (304, 541, 778, 1015)
    BAG_ROW_TOPS = (110, 228, 350, 471)
    BAG_TEXT_DX = 48
    BAG_TEXT_SIZE = (152, 118)
    BAG_COUNT_DX = 22
    BAG_COUNT_DY = 55
    BAG_COUNT_SIZE = (35, 30)

    def bag_cell_regions(self, col_x, row_top):
        """返回该物品格的 (文字区域, 数量区域)，格式 x,y,w,h"""
        text_region = (col_x + self.BAG_TEXT_DX, row_top,
                       self.BAG_TEXT_SIZE[0], self.BAG_TEXT_SIZE[1])
        count_region = (col_x + self.BAG_COUNT_DX, row_top + self.BAG_COUNT_DY,
                        self.BAG_COUNT_SIZE[0], self.BAG_COUNT_SIZE[1])
        return text_region, count_region

    def bag_back_to_top(self) -> None:
        """
        回到背包顶部：先去别的页面再回来，背包会自动滚到顶部
        """
        self.ui_goto(page_role_detail, timeout=20)
        self.device.sleep(0.3)
        self.ui_goto(page_item_bag, timeout=20)
        self.device.sleep(self.BAG_SETTLE)

    # 碎片数字的 OCR 变体（「一」常被认成 —/－/1）
    NUMERAL_VARIANTS = {
        '一': ('碎片一', '碎片—', '碎片－', '碎片-', '碎片1'),
        '二': ('碎片二', '碎片2'),
        '三': ('碎片三', '碎片3'),
        '四': ('碎片四', '碎片4'),
    }

    @classmethod
    def parse_numeral(cls, text: str):
        """从文本里认碎片数字（容忍「一」被认成「—」）"""
        for numeral, variants in cls.NUMERAL_VARIANTS.items():
            if any(variant in text for variant in variants):
                return numeral
        return None

    def bag_page_boxes(self):
        """整块背包网格 OCR -> [(文本, 左边缘x, 中心y)]（补充手段，容忍边界行）"""
        rule = self.O_BAG_GRID
        results = rule.detect_and_ocr(self.device.image, logDisplay=False)
        roi = rule.roi
        boxes = []
        for item in results:
            box = item.box
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            boxes.append((item.ocr_text.strip(), min(xs) + roi[0], (min(ys) + max(ys)) / 2 + roi[1]))
        return boxes

    def match_piece_boxes(self, boxes, color: str, found: dict) -> None:
        """
        整块 OCR 的内容配对（补充固定区域）：颜色行「XX仙玉」附近找「碎片X」，再读邻近数字

        只在本屏内配对（跨屏无法校验颜色，会把别的颜色的碎片配错）
        """
        for text, x, y in boxes:
            if '仙玉' not in text or color not in text:
                continue
            numeral = self.parse_numeral(text)
            count = None
            if numeral is None:
                for text2, x2, y2 in boxes:
                    if abs(x2 - x) > 120 or abs(y2 - y) > 80:
                        continue
                    numeral = self.parse_numeral(text2)
                    if numeral:
                        match = re.match(r'\s*(\d+)', text2)
                        count = int(match.group(1)) if match else None
                        break
            if numeral is None:
                continue
            if count is None:
                for text2, x2, y2 in boxes:
                    digits = text2.strip()
                    if not digits.isdigit():
                        continue
                    if abs(x2 - x) <= 70 and 15 <= (y2 - y) <= 75:
                        count = max(count or 0, int(digits))
            idx = NUMERALS.index(numeral) + 1
            if count:
                found[idx] = max(found.get(idx, 0), count)
            else:
                found.setdefault(idx, 1)

    def bag_region_digits(self, region) -> int:
        """
        读数量区域里的数字（小字，转灰度放大 2x/3x 再识别），读不到返回 0

        数量为 1 时游戏不显示数字，调用方把 0 当 1 处理。
        """
        x, y, w, h = region
        image = self.device.image[y:y + h, x:x + w]
        if image is None or image.size == 0:
            return 0
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        best = 0
        for scale in (2, 3):
            resized = cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), None,
                                 fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            result = self.O_BAG_GRID.model.ocr_single_line(resized)
            if not result:
                continue
            text, _ = result
            digits = re.sub(r'\D', '', str(text))
            if digits:
                best = max(best, int(digits))
        return best

    def bag_piece_counts(self, color: str) -> dict:
        """
        读背包里该颜色仙玉碎片一二三四的数量

        - 回顶：先去别的页面再回背包（游戏会自动回顶）
        - 翻页：`device.swipe((490,570) -> (490,80))`，一页 4 行
        - 识别：每个物品格用固定文字区/数量区（见 BAG_* 常量）
        - 数量为 1 时游戏不显示数字，所以「有名字但没读到数字」按 1 算
        :return: {1: 数量, 2: 数量, 3: 数量, 4: 数量}（物品不存在按 0）
        """
        logger.hr(f'读背包 {color}仙玉碎片数量')
        if not self.ui_goto(page_item_bag, timeout=40):
            raise GameStuckError('Item bag page does not appear')
        self.device.sleep(self.BAG_SETTLE)
        self.bag_close_item_panel()
        self.bag_sort()
        self.bag_back_to_top()

        found = {}
        found_sup = {}
        last_signature = None
        for page in range(1, self.BAG_SCAN_SCREENS + 1):
            self.screenshot()
            page_texts = []
            for row_top in self.BAG_ROW_TOPS:
                for col_x in self.BAG_COLUMN_CENTERS:
                    text_region, count_region = self.bag_cell_regions(col_x, row_top)
                    text = self.bag_region_text(text_region)
                    if not text:
                        continue
                    page_texts.append(text)
                    if color not in text:
                        continue
                    numeral = self.parse_numeral(text)
                    if numeral is None:
                        # 「一」是一横，OCR 常整个丢掉（读成「北极仙玉碎片」），这里按「一」兜底
                        if f'{color}仙玉碎片' in text:
                            logger.info(f'「{text}」没读到碎片数字，按「一」处理')
                            numeral = '一'
                        else:
                            continue
                    count = self.bag_region_digits(count_region)
                    # 数量为 1 时游戏不显示数字
                    count = count if count else 1
                    idx = NUMERALS.index(numeral) + 1
                    found[idx] = max(found.get(idx, 0), count)
            # 补充：整块 OCR 内容配对（固定区域在翻页边界会漏行），只用来补空缺
            boxes = self.bag_page_boxes()
            self.match_piece_boxes(boxes, color, found_sup)
            logger.info(f'第{page}页物品: {page_texts}')
            if not page_texts:
                logger.info('背包已到空行')
                break
            if self.appear(self.I_BAG_EMPTY):
                logger.info('本页出现空物品格（物品已按从有到无排序），下面没有物品了')
                break
            signature = tuple(page_texts)
            if signature == last_signature:
                logger.info('背包没有继续滚动（可能开了面板或到底），停止')
                break
            last_signature = signature
            self.device.swipe(p1=(490, 570), p2=(490, 80))
            self.device.click_record_clear()
            self.device.sleep(1.2)

        # 固定区域优先，补充只补空缺
        result = {}
        for i in range(len(NUMERALS)):
            idx = i + 1
            if idx in found:
                result[idx] = found[idx]
            elif idx in found_sup:
                result[idx] = found_sup[idx]
            else:
                result[idx] = 0
        logger.attr(f'{color}仙玉碎片数量', result)
        return result

    @staticmethod
    def pick_target(counts: dict) -> int:
        """选数量最少的碎片去打；数量一样优先大的号（都没有就缺 4 先打大关4）"""
        return min((1, 2, 3, 4), key=lambda n: (counts.get(n, 0), -n))

    # ---------------------------------------------------------------- 一场战斗
    def run_one_battle(self, daguan: int, xiaoguan: int, coord_text: str) -> bool:
        logger.hr(f'{DAGUAN_NAMES[daguan]} 第{xiaoguan}小关')
        self.enter_xianfu_page()
        if not self.select_daguan(daguan):
            return False
        state = self.open_stage(daguan, xiaoguan)
        if state is None:
            return False
        if state == 'reward':
            # 该关卡有未领取的宝箱，先领奖（不消耗仙石）
            logger.info('该关卡有未领取的奖励，直接翻牌')
            self.claim_reward()
            return True
        if not self.start_challenge():
            return False
        if not self.battle(self.parse_coord(coord_text)):
            return False
        if self.claim_reward():
            return True
        # 领奖失败：已通关的关卡上是个宝箱，再点一次可以重新翻牌（不再消耗仙石）
        logger.warning('领奖失败，再点一次当前关卡重新翻牌')
        if self.open_stage_reward(daguan, xiaoguan):
            self.claim_reward()
        return True

    def open_stage_reward(self, daguan: int, xiaoguan: int, timeout: int = 12) -> bool:
        """
        点已通关的关卡（宝箱），等翻牌面板再次出现（不消耗仙石）
        """
        logger.hr('重新打开领奖面板')
        stage = getattr(self, f'C_S{daguan}_{xiaoguan}')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_REWARD_PANEL):
                return True
            x, y = stage.coord()
            self.device.click(x=x, y=y, control_name=stage.name)
            self.device.click_record_clear()
            self.device.sleep(0.8)
        logger.warning('领奖面板没有再出现')
        return False

    def select_daguan(self, daguan: int, timeout: int = 6) -> bool:
        """
        点左侧大关按钮

        注意：已通关的岛屿上会盖着宝箱，可能挡住大关特征图，所以特征只做软校验
        （点几次没切过去也继续，后面确认关卡详情时会兜底发现）
        """
        logger.hr(f'选择大关 {DAGUAN_NAMES[daguan]}')
        button = getattr(self, f'C_DAGUAN_TAB_{daguan}')
        feature = getattr(self, f'I_DAGUAN_{daguan}')
        for _ in range(3):
            self.reset_records()
            self.screenshot()
            if self.appear(feature):
                return True
            x, y = button.coord()
            self.device.click(x=x, y=y, control_name=button.name)
            self.device.click_record_clear()
            self.device.sleep(1.2)
        self.screenshot()
        if self.appear(feature):
            return True
        logger.warning(f'{DAGUAN_NAMES[daguan]} feature does not appear, continue anyway')
        return True

    def open_stage(self, daguan: int, xiaoguan: int, timeout: int = 12):
        """
        点小关岛屿，返回：
        - 'detail'：出现关卡详情（可以开始挑战）
        - 'reward'：出现翻牌领奖面板（该关卡有未领取的宝箱）
        - None：都没出现
        """
        logger.hr(f'打开 {DAGUAN_NAMES[daguan]} 第{xiaoguan}小关')
        stage = getattr(self, f'C_S{daguan}_{xiaoguan}')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_START_CHALLENGE):
                return 'detail'
            if self.appear(self.I_REWARD_PANEL):
                return 'reward'
            x, y = stage.coord()
            self.device.click(x=x, y=y, control_name=stage.name)
            self.device.click_record_clear()
            self.device.sleep(0.8)
        logger.warning('关卡详情/领奖面板都没有出现')
        return None

    def start_challenge(self, timeout: int = 15) -> bool:
        logger.hr('开始挑战')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear_then_click(self.I_START_CHALLENGE, interval=1):
                self.device.sleep(1.5)
                return True
            self.device.sleep(0.5)
        logger.warning('Start challenge button not found')
        return False

    # ---------------------------------------------------------------- 战斗
    def in_xianfu_map(self):
        location = self.map_current_location()
        if location is None:
            return None
        return self.map_name_match(location[0], MAP_NAME)

    def battle(self, target_coord) -> bool:
        logger.hr('战斗')
        timer = Timer(25).start()
        while not timer.reached():
            self.reset_records()
            if self.in_xianfu_map():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('副本未加载')
            return False

        logger.info(f'走到怪物 {target_coord}')
        # calibrate=False：副本限时 2 分钟，不想花时间做校准探测（用登记的校准值/兜底）
        # 返回值必须看：走不到就只会在原地放技能，日志里要有明确记录
        if not self.map_move_to(target_coord[0], target_coord[1],
                                ensure_main=False, calibrate=False, timeout=60):
            logger.warning(f'走到怪物 {target_coord} 失败，将在当前位置放技能')

        def stop_check():
            self.reset_records()
            self.screenshot()
            return self.appear(self.I_REWARD_PANEL)

        logger.info('开始释放技能（7 技能 + 普通攻击，每组顺序随机）')
        return self.battle_simple_loop(stop_check=stop_check,
                                       timeout=BATTLE_TIMEOUT,
                                       groups_max=BATTLE_GROUPS_MAX)

    # ---------------------------------------------------------------- 领奖
    # 本次翻牌奖励的原文（用于判断是否还没翻开）
    last_reward_text = ''

    def read_reward(self):
        """读「恭喜您获得 XXX」里的碎片，返回 (颜色, 数字)；读不到返回 None"""
        self.screenshot()
        results = self.O_XIANFU_REWARD.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(item.ocr_text for item in results)
        self.last_reward_text = text
        logger.attr('翻牌奖励OCR', text)
        for color in ('南极', '北极', '东极', '西极'):
            for num in NUMERALS:
                if color in text and f'碎片{num}' in text:
                    return color, num
        return None

    def claim_reward(self, timeout: int = 25) -> bool:
        logger.hr('翻牌领奖')
        self.last_reward = None
        self.screenshot()
        if not self.appear(self.I_REWARD_PANEL):
            logger.warning('奖励面板未出现')
            return False

        # 面板有入场动画，先等一下再翻；如果 OCR 还是「选择翻开…」提示就再点一次
        self.device.sleep(1.5)
        for attempt in range(1, 4):
            x, y = self.C_REWARD_CARD.coord()
            logger.info(f'翻牌 ({x},{y}) 第{attempt}次')
            self.device.click(x=x, y=y, control_name='XIANFU_FLIP')
            self.device.click_record_clear()
            self.device.sleep(2.5)
            self.last_reward = self.read_reward()
            if self.last_reward is not None:
                break
            if '选择翻开' not in (self.last_reward_text or ''):
                logger.info('不是「选择翻开」提示，停止重复翻牌')
                break

        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if not self.appear(self.I_REWARD_PANEL):
                logger.info('奖励面板已关闭')
                return True
            # 先关可能弹出的物品详情浮层，再关奖励面板
            self.device.click(x=970, y=240, control_name='XIANFU_TOOLTIP_CLOSE')
            cx, cy = self.C_POPUP_CLOSE.coord()
            self.device.click(x=cx, y=cy, control_name='XIANFU_REWARD_CLOSE')
            self.device.click_record_clear()
            self.device.sleep(0.8)
        logger.warning('奖励面板关不掉')
        return False

    # ---------------------------------------------------------------- 导航
    def enter_xianfu_page(self, timeout: int = 45) -> bool:
        self.ui_reset_current_page()
        if not self.ui_goto(page_xianfu, timeout=timeout):
            raise GameStuckError('Xianfu page does not appear')
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
