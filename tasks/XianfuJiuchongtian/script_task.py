# This Python file uses the following encoding: utf-8
"""
仙府九重天（XianfuJiuchongtian）

按配置的「阶段」收集对应颜色的仙玉碎片：打各大关的第 N 小关，小关 N 大概率掉颜色 N 的碎片
（1=南极、2=北极、3=东极、4=西极）；打大关 M 只会掉「仙玉碎片M」
（如持国天=M3 只掉碎片三）。翻牌颜色随机，所以按缺失的号从大到小打：
缺 4 → 打大关4的小关N，反复打到拿到碎片4，再打大关3拿碎片3 …… 集齐后重新从大关4开始。
配置 `fixed_daguan`（1-4）可直接指定大关，指定后跳过背包统计，一直打该大关的第 N 小关。
背包统计：先用对应颜色的模板（图标 + 「XX仙玉」）定位碎片，没命中视为没有；命中后只 OCR
命中格的碎片编号和数量。

仙石：每半小时 +1、上限 40。达到 stone_threshold 才开始清仙石，循环打到仙石 < 4 为止，
然后按 (阈值 - 当前仙石) * 30 分钟排下一次运行。

流程：挑战-仙府九重天页 -> 选大关 -> 选小关 -> 开始挑战
      -> 进图后打开小地图，点战斗点让角色自动寻路到怪物，然后关闭小地图
      -> 收菜单锁定怪物（右下角按钮必须是「攻击」）；15s 内没进入战斗就重新点小地图移动
      -> 打怪：按优先级队列释放技能，每个技能双击；
         没进冷切就排到下一个技能后面（同时只允许一个延后），全部技能冷却时才普攻
      -> 目标丢失/奖励面板出现即停 -> 翻牌领奖 -> 关面板，循环直到仙石不够。
      若目标丢失后回到仙府页面仍没有奖励面板，按本关未通过处理：推送通知并继续下一轮。
"""
import re
from datetime import datetime, timedelta

import cv2

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_item_bag
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
# 目标消失后等奖励面板弹出的时间（秒）：怪物死亡 -> 面板弹出有一点延迟
REWARD_PANEL_GRACE = 3.0
# 目标丢失后等「奖励面板 / 回到仙府页面」的时间（秒）：没打过时会被传回仙府页面
STAGE_RESULT_TIMEOUT = 10.0


class ScriptTask(GameUi, XianfuJiuchongtianAssets):

    # 本次翻牌结果 (color, numeral)；每场战斗更新
    last_reward = None
    # 读背包最多扫多少屏（整页滑动，一页 4 行）
    BAG_SCAN_SCREENS = 12

    def run(self) -> None:
        stage, threshold, max_battles_cfg, fixed_daguan = self.load_config()
        color = XIAOGUAN_COLORS[stage]
        daguan_text = f'指定大关{fixed_daguan}' if fixed_daguan else '按背包自动选'
        logger.hr(f'仙府九重天 阶段{stage}({color}) 阈值{threshold} '
                  f'最多{max_battles_cfg or "不限"}局 {daguan_text}')

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

        if fixed_daguan:
            # 指定了大关就不需要读背包（省掉一次整包扫描和翻页）
            counts = {}
            logger.info(f'已指定大关 {fixed_daguan}（{DAGUAN_NAMES[fixed_daguan]}），跳过背包统计')
        else:
            counts = self.bag_piece_counts(color)
            logger.attr(f'{color}仙玉碎片数量', counts)
        max_battles = stone // STONE_COST + 3
        if max_battles_cfg > 0:
            max_battles = min(max_battles, max_battles_cfg)
        battles = 0
        while stone is not None and stone >= STONE_COST and battles < max_battles:
            if fixed_daguan:
                target = fixed_daguan
                logger.info(f'目标（指定）：{DAGUAN_NAMES[target]}(大关{target}) 第{stage}小关')
            else:
                target = self.pick_target(counts)
                logger.info(f'目标：{DAGUAN_NAMES[target]}(大关{target}) 第{stage}小关，'
                            f'{color}仙玉碎片{NUMERALS[target - 1]} 当前 {counts.get(target, 0)}')
            if not self.run_one_battle(target, stage):
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
                    int(cfg.fixed_daguan))
        except Exception as e:  # noqa: BLE001
            logger.warning(f'读取配置失败({e})，用默认值 阶段1/阈值40/不限局数/大关自动')
            return 1, 40, 0, 0

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

    # 颜色 -> 该颜色碎片的模板规则（模板 = 图标 + 第一行「XX仙玉」）
    PICK_RULES = {
        '西极': 'I_BAG_PICK_WEST',
        '东极': 'I_BAG_PICK_EAST',
        '北极': 'I_BAG_PICK_NORTH',
        '南极': 'I_BAG_PICK_SOUTH',
    }
    # 模板命中框 -> 格子图标中心的偏移（四张模板实测约 25px）
    PICK_ICON_DX = 25
    # 格子图标中心相对行顶的偏移（行高 120，图标约 50 高）
    PICK_ICON_HALF = 25

    def bag_cell_regions(self, col_x, row_top):
        """返回该物品格的 (文字区域, 数量区域)，格式 x,y,w,h"""
        text_region = (col_x + self.BAG_TEXT_DX, row_top,
                       self.BAG_TEXT_SIZE[0], self.BAG_TEXT_SIZE[1])
        count_region = (col_x + self.BAG_COUNT_DX, row_top + self.BAG_COUNT_DY,
                        self.BAG_COUNT_SIZE[0], self.BAG_COUNT_SIZE[1])
        return text_region, count_region

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

    def bag_cell_from_hit(self, x, y, w, h):
        """
        模板命中框 -> 所在格子的 (列图标 x, 行 top)

        模板是「图标 + 第一行名字」：命中框左边约在图标中心 -PICK_ICON_DX，
        框中心 y 约在图标中心，换算后吸附到最近的行列。
        """
        icon_x = x + self.PICK_ICON_DX
        icon_y = y + h / 2
        col = min(self.BAG_COLUMN_CENTERS, key=lambda c: abs(c - icon_x))
        row_top = min(self.BAG_ROW_TOPS, key=lambda r: abs(r - (icon_y - self.PICK_ICON_HALF)))
        return col, row_top

    def bag_page_signature(self, image) -> bytes:
        """背包页的粗粒度指纹：滑动后内容没变就说明到底了（比整页 OCR 便宜）"""
        x, y, w, h = self.O_BAG_GRID.roi
        gray = cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY)
        return cv2.resize(gray, (64, 32), interpolation=cv2.INTER_AREA).tobytes()

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
        读背包里该颜色仙玉碎片一二三四的数量（模板定位 + 只 OCR 命中格）

        同色碎片在背包里连续排列：用该颜色的「图标 + XX仙玉」模板逐页找命中，
        - 没命中：该颜色没有碎片，返回全 0（不再整包 OCR）；
        - 命中：只对命中的格子 OCR「碎片X」和数量（数量为 1 时不显示数字，按 1 算）。

        :return: {1: 数量, 2: 数量, 3: 数量, 4: 数量}（不存在的碎片按 0）
        """
        logger.hr(f'读背包 {color}仙玉碎片数量（模板定位）')
        rule_name = self.PICK_RULES.get(color)
        rule = getattr(self, rule_name, None) if rule_name else None
        result = {i: 0 for i in range(1, len(NUMERALS) + 1)}
        if rule is None:
            logger.warning(f'没有 {color} 的碎片模板（{rule_name}），视为没有碎片')
            return result

        if not self.ui_goto(page_item_bag, timeout=40):
            raise GameStuckError('Item bag page does not appear')
        self.device.sleep(self.BAG_SETTLE)
        self.bag_close_item_panel()
        self.bag_sort()

        found_any = False
        last_signature = None
        for page in range(1, self.BAG_SCAN_SCREENS + 1):
            self.screenshot()
            image = self.device.image
            hits = rule.match_all_any(image, threshold=rule.threshold)
            page_cells = set()
            page_hit = False
            for score, x, y, w, h in sorted(hits, key=lambda hit: -hit[0]):
                cell = self.bag_cell_from_hit(x, y, w, h)
                if cell in page_cells:
                    continue
                page_cells.add(cell)
                col_x, row_top = cell
                text_region, count_region = self.bag_cell_regions(col_x, row_top)
                text = self.bag_region_text(text_region)
                numeral = self.parse_numeral(text)
                if numeral is None:
                    # 「一」是一横，OCR 常整个丢掉；命中格有这个颜色就按「一」兜底
                    if f'{color}仙玉碎片' in text:
                        numeral = '一'
                    else:
                        logger.warning(f'命中格读不到碎片编号: {text!r} @ ({x},{y})')
                        continue
                count = self.bag_region_digits(count_region) or 1
                idx = NUMERALS.index(numeral) + 1
                result[idx] = max(result[idx], count)
                page_hit = found_any = True
                logger.info(f'命中 {color}仙玉碎片{numeral} x{count} '
                            f'@ ({x},{y}) score={score:.3f}')
            if not page_hit:
                logger.info(f'第{page}页未命中 {color} 碎片模板')

            # 翻页停止：空物品格 / 内容不再变化 / 页数上限
            if self.appear(self.I_BAG_EMPTY):
                logger.info('本页出现空物品格，停止翻页')
                break
            signature = self.bag_page_signature(image)
            if signature == last_signature:
                logger.info('背包没有继续滚动，停止')
                break
            last_signature = signature
            self.device.swipe(p1=(490, 570), p2=(490, 80))
            self.device.click_record_clear()
            self.device.sleep(1.2)

        if not found_any:
            logger.info(f'模板未命中，视为没有 {color} 仙玉碎片')
        logger.attr(f'{color}仙玉碎片数量', result)
        return result

    @staticmethod
    def pick_target(counts: dict) -> int:
        """选数量最少的碎片去打；数量一样优先大的号（都没有就缺 4 先打大关4）"""
        return min((1, 2, 3, 4), key=lambda n: (counts.get(n, 0), -n))

    # ---------------------------------------------------------------- 一场战斗
    def run_one_battle(self, daguan: int, xiaoguan: int) -> bool:
        logger.hr(f'{DAGUAN_NAMES[daguan]} 第{xiaoguan}小关')
        # 每局开始重置翻牌结果，避免本局没打到/没领奖时沿用上一局的碎片计数
        self.last_reward = None
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
        result = self.battle()
        if result not in ('reward', 'target_lost'):
            logger.warning('战斗失败（副本未加载 / 小地图移动失败 / 超时），停止清仙石')
            return False
        if result == 'target_lost':
            # 目标丢失后看最终结果：回到仙府页面且没有奖励面板 = 这关没打过
            outcome = self.wait_stage_result()
            if outcome == 'failed':
                self.notify_stage_failed(daguan, xiaoguan)
                return True  # 继续下一轮
            if outcome == 'reward':
                logger.info('目标丢失后奖励面板出现，继续领奖')
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
    def battle_task_preset(self) -> str:
        """
        本任务选的战斗预设（配置 -> battle_config.battle_preset）

        共享的 BattleAttack.battle_task_preset() 取的是任务根节点的 battle_preset，
        取不到嵌套在 battle_config 组里的字段，会静默回落到系统预设。
        先在仙府任务里覆盖，只影响本任务。
        """
        return str(self.config.xianfu_jiuchongtian.battle_config.battle_preset or '')

    def in_xianfu_map(self):
        location = self.map_current_location()
        if location is None:
            return None
        return self.map_name_match(location[0], MAP_NAME)

    # 关闭小地图后，多少秒内没进入战斗就重新打开小地图移动
    BATTLE_ENTER_TIMEOUT = 15
    # 小地图移动最多重试几次（防止点不到战斗点时死循环）
    BATTLE_MOVE_RETRY = 5

    def battle(self) -> str:
        """
        进副本打怪

        - 进图后打开小地图，点战斗点让角色自动寻路到怪物，然后关闭小地图
        - 关闭小地图后 BATTLE_ENTER_TIMEOUT 秒内锁定到可攻击目标就开始战斗；
          超时还没进入战斗就重新打开小地图再点一次

        :return: 'reward' 奖励面板出现；'target_lost' 目标丢失；'failed' 副本未加载/移动失败/超时
        """
        logger.hr('战斗')
        timer = Timer(25).start()
        while not timer.reached():
            self.reset_records()
            if self.in_xianfu_map():
                break
            self.device.sleep(0.5)
        else:
            logger.warning('副本未加载')
            return 'failed'

        for attempt in range(1, self.BATTLE_MOVE_RETRY + 1):
            logger.info(f'打开小地图，点击战斗点（第{attempt}次）')
            self.map_close_main_popup()
            if not self.map_open_minimap():
                logger.warning('小地图没有打开')
                continue
            self.click(self.C_BATTLE_POINT)
            self.device.sleep(0.5)
            self.map_close_minimap()

            logger.info(f'等待进入战斗（{self.BATTLE_ENTER_TIMEOUT}s）')
            # max_switch 放大，让 15s 超时而不是切换次数先到
            if self.ui_ensure_target_locked(timeout=self.BATTLE_ENTER_TIMEOUT, max_switch=30):
                logger.info('已锁定可攻击目标，开始释放技能'
                            '（队列式：双击 -> 校验目标/冷切；全部冷却才普攻）')
                return self.battle_loop()
            logger.warning(f'{self.BATTLE_ENTER_TIMEOUT}s 内没有进入战斗，重新打开小地图移动')

        logger.warning(f'重试 {self.BATTLE_MOVE_RETRY} 次仍未进入战斗，结束战斗')
        return 'failed'

    # ---------------------------------------------------------------- 战斗循环
    # 每个技能最多尝试几次（含第一次）：每次 = 一次双击，没进冷却就重试，超过跳过换下一个技能
    SKILL_MAX_ATTEMPTS = 3
    # 点完技能等它进入冷却的最长时间 / 轮询间隔（秒）
    SKILL_COOLDOWN_TIMEOUT = 1.2
    SKILL_COOLDOWN_POLL = 0.1

    def battle_loop(self) -> str:
        """
        队列式连续释放技能，直到奖励面板出现 / 目标丢失 / 超时 / 重排次数上限

        - 按优先级顺序释放；没进冷却的技能排到下一个技能后面（同时只允许一个延后）；
        - 下一个技能也没进冷却时不重排，继续释放之前延后的那个；
        - 所有技能都在冷却时才释放普通攻击。

        :return: 'reward' 奖励面板出现；'target_lost' 目标丢失；'timeout' 超时/重排上限
        """
        self.screenshot()
        plan = self.battle_plan()
        if plan:
            slots = [entry.slot for entry in plan]
        else:
            logger.warning('技能位识别失败，按 7 个技能位逐个尝试')
            slots = list(range(len(self.BATTLE_SKILL_REGIONS)))

        return self.battle_skill_loop(
            slots,
            check_stop=self.battle_stop_reason,
            target_check=lambda image: self.ui_target_attackable(image),
            timeout=BATTLE_TIMEOUT,
            max_attempts=self.SKILL_MAX_ATTEMPTS,
            cooldown_timeout=self.SKILL_COOLDOWN_TIMEOUT,
            poll_interval=self.SKILL_COOLDOWN_POLL,
            max_cycles=BATTLE_GROUPS_MAX,
        )

    def battle_stop_reason(self, image=None) -> str | None:
        """
        战斗该结束了吗：'reward' 奖励面板出现；'target_lost' 目标丢失且等不到面板

        :param image: 调用方传入的截图（同一帧已经用来识别目标/按钮/冷切）；
                      None = 自己现截。奖励面板与目标/按钮都基于这张图判断。
        """
        self.reset_records()
        if image is None:
            image = self.screenshot()
        if self.I_REWARD_PANEL.match(image):
            logger.info('奖励面板出现，结束战斗')
            return 'reward'
        if self.ui_target_attackable(image):
            return None
        # 目标丢失：多半是打死了，给奖励面板一点弹出时间，别抢在面板前结束
        timer = Timer(REWARD_PANEL_GRACE).start()
        while not timer.reached():
            self.device.sleep(0.3)
            image = self.screenshot()
            if self.I_REWARD_PANEL.match(image):
                logger.info('奖励面板出现，结束战斗')
                return 'reward'
        logger.warning('目标丢失且等待奖励面板超时，结束战斗')
        return 'target_lost'

    def wait_stage_result(self, timeout: float = None) -> str | None:
        """
        战斗以「目标丢失」结束后，确认这一关的最终结果：

        - 'reward'：奖励面板出现（其实已通关，继续领奖）
        - 'failed'：回到仙府九重天页面且没有奖励面板（没打过，被传回来了）
        - None：超时都没确认（按原流程继续尝试领奖）

        :param timeout: 等待时间（秒），默认 STAGE_RESULT_TIMEOUT
        """
        timeout = STAGE_RESULT_TIMEOUT if timeout is None else float(timeout)
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_REWARD_PANEL):
                logger.info('奖励面板出现')
                return 'reward'
            if self.ui_page_appear(page_xianfu, skip_first_screenshot=True):
                # 页面出现后再确认一下面板（面板可能比页面晚一点弹出）
                for _ in range(4):
                    self.device.sleep(0.5)
                    self.screenshot()
                    if self.appear(self.I_REWARD_PANEL):
                        logger.info('奖励面板出现')
                        return 'reward'
                logger.info('已回到仙府九重天页面，且没有奖励面板')
                return 'failed'
            self.device.sleep(0.5)
        logger.warning(f'{timeout}s 内没等到奖励面板，也没回到仙府九重天页面')
        return None

    def notify_stage_failed(self, daguan: int, xiaoguan: int) -> None:
        """关卡未通过：推送通知（推送失败不影响继续下一轮）"""
        message = (f'{DAGUAN_NAMES[daguan]} 第{xiaoguan}小关未通过'
                   f'（目标丢失后回到仙府九重天页面，没有奖励面板）')
        logger.warning(message)
        self.config.notifier.push(title='仙府九重天任务关卡未通过',
                                  content=f'<{self.config.config_name}> {message}')

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

