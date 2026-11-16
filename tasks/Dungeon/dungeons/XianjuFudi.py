# This Python file uses the following encoding: utf-8
"""
仙居福地 副本流程

外地图：骨牢（可直接传送，没有独立外地图）。万事通和副本入口都在骨牢小地图
视口**右上角**：
- 万事通     (851,172,24,22)
- 副本入口   (797,145,24,22)

副本内：地图名 仙居福地[副本]，刚进图坐标 (156,273)。
和前几个副本不同，这里 5 个 Boss 各自在一个房间里，流程是「进房间 -> 走到 Boss」：

    Boss  房间入口（小地图左上角）  进房后落点     Boss 坐标
    焱    388,508,30,27             142,243       63,190
    垚    574,573,30,27             252,284       331,373
    森    553,364,30,27             238,160       163,89
    淼    720,476,28,23             338,224       440,273
    鑫    709,363,28,23             330,156       419,85

- 进房间沿用通用操作：`dungeon_minimap_walk()`（点小地图房间入口 -> 等走到落点坐标）；
- 房间里没有可点的 Boss 图标，改用 `dungeon_go_to_position()`
  （底层是 `GameUi.map_go_to_coordinate`，按坐标点地面自动寻路）；
  走位时**同时尝试锁敌**（`lock_targets`，轮询回调），一锁到就停下开打 ——
  游戏里锁定目标后点攻击会自动走到目标脚下，不需要非得走到坐标点上；
- 打完一只 Boss **能直接点下一个房间门**（不用先走出房间），所以每个房间都是
  「点门 -> 等落点 -> 走位 -> 打」四步循环；
- 出口：小地图视口左上角 (812,198,28,23)，回到骨牢。
- 副本前准备顺序：骨牢 -> **修理装备 -> 万事通治疗**（先修后治）。

死亡：**副本内会弹死亡框**（沙狱/帝玉岭是直接退出副本），复活后人物回到骨牢。
      这一局按「完成一次」收尾（不推「未通关」、不跳过剩余次数），详见
      `dungeon_left_for_death()` / `finish_run_after_death()`。
"""
from module.logger import logger
from tasks.Dungeon.common import DungeonBase, DungeonRetry


class XianjuFudi(DungeonBase):
    name = '仙居福地'
    FIELD = 'xianju_fudi'
    # 右上角地点 OCR 用：名称里的「[副本]」在 map_name_match 里会被忽略，带不带括号都能匹配
    MAP_NAME = '仙居福地[副本]'
    # 小地图坐标标定 / map_go_to_coordinate 用的名字：OCR 读到的地点名不带括号，
    # maps.json 也是按「仙居福地副本」录的，所以这里用不带括号的
    MAP_CALIBRATION_NAME = '仙居福地副本'
    ENTRY_POSITION = (156, 273)
    EXIT_MAP = '骨牢'
    # 外地图（骨牢）里：万事通 / 副本入口都在视口右上角
    ENTRY_CORNER = 'top_right'
    # 副本内：房间入口 / 出口都在视口左上角
    ROOM_CORNER = 'top_left'
    EXIT_CORNER = 'top_left'
    ENTRY_CLICK_NAME = 'xianju_fudi_entry'
    EXIT_CLICK_NAME = 'xianju_fudi_exit'

    # 进房间后等落点的容差（格）：进房间是走进去的，不会特别精确
    ROOM_ARRIVE_TOLERANCE = 10
    # 房间内走到 Boss 的容差（格）
    BOSS_POSITION_TOLERANCE = 3

    # 5 个 Boss 都是生僻字，OCR 容易读成常见字；不登记别名会「锁到但名字对不上」
    BOSS_NAME_ALIASES = {
        '焱': ('炎',),
        '垚': ('圭', '土'),
        '森': ('林',),
        '淼': ('水',),
        '鑫': ('金',),
    }

    # (Boss 名, 房间入口点击规则, 进房后的落点坐标, 房间内 Boss 坐标)
    ROOMS = (
        ('焱', 'xianju_fudi_room_1', (142, 243), (63, 190)),
        ('垚', 'xianju_fudi_room_2', (252, 284), (331, 373)),
        ('森', 'xianju_fudi_room_3', (238, 160), (163, 89)),
        ('淼', 'xianju_fudi_room_4', (338, 224), (440, 273)),
        ('鑫', 'xianju_fudi_room_5', (330, 156), (419, 85)),
    )

    def run_once(self) -> bool:
        logger.hr(f'[{self.name}] 完成一次')
        # 副本前准备（进外地图 -> 修理装备 -> 万事通治疗），顺序统一在
        # DungeonBase.dungeon_prepare_run() 里维护
        if not self.dungeon_prepare_run(self.FIELD):
            return False

        entry_rule = getattr(self, f'C_{self.ENTRY_CLICK_NAME.upper()}')
        if not self.dungeon_minimap_walk(self.ENTRY_CORNER, entry_rule,
                                         self.MAP_NAME, self.ENTRY_POSITION):
            logger.warning('Unable to enter dungeon map')
            return False
        self._entered_dungeon = True
        return self.run_from_inside()

    def run_from_inside(self) -> bool:
        """从初始坐标开始，逐房间打 5 个 Boss，最后从出口回骨牢"""
        finished = False
        for boss_name, room_rule_name, room_position, boss_position in self.ROOMS:
            self.dungeon_check_in_map(self.MAP_NAME)
            if self.dungeon_left_for_death():
                return self.finish_run_after_death()
            if self.dungeon_finish_detected():
                logger.info('Dungeon finished before next boss')
                finished = True
                break
            # 上一个 Boss 的奖励弹窗可能延迟出现，先清掉再移动
            self.dungeon_close_boss_reward()

            if not self.enter_room(boss_name, room_rule_name, room_position):
                if self.dungeon_left_for_death():
                    return self.finish_run_after_death()
                return False
            # 房间内没有小地图 Boss 图标：按坐标点地面自动寻路，
            # 走位途中就尝试锁敌（锁到就停下开打，游戏会自己走到目标脚下）
            if not self.dungeon_go_to_position(boss_name, boss_position,
                                               map_name=self.MAP_CALIBRATION_NAME,
                                               tolerance=self.BOSS_POSITION_TOLERANCE,
                                               lock_targets=self._dungeon_target_names(boss_name)):
                if self.dungeon_left_for_death():
                    return self.finish_run_after_death()
                logger.warning(f'Unable to walk to boss [{boss_name}] in room')
                return False

            result = self.dungeon_fight_boss(boss_name, map_name=self.MAP_NAME)
            # 战斗中死亡：复活后已回骨牢，这局不再继续
            if self.dungeon_left_for_death():
                return self.finish_run_after_death()
            logger.info(f'Boss [{boss_name}] result: {result}')
            if result == 'finished':
                finished = True
                break
            if result != 'killed':
                logger.warning(f'Boss [{boss_name}] fight failed: {result}')
                return False

        if not finished and self.dungeon_finish_detected():
            finished = True
        if finished:
            self.dungeon_close_result_popups()

        # 离开副本：小地图左上角出口 -> 骨牢
        exit_rule = getattr(self, f'C_{self.EXIT_CLICK_NAME.upper()}')
        if not self.dungeon_minimap_walk(self.EXIT_CORNER, exit_rule, self.EXIT_MAP):
            # 完成/奖励弹窗挡住小地图时，先关弹窗再重试一次
            self.dungeon_close_result_popups()
            if not self.dungeon_minimap_walk(self.EXIT_CORNER, exit_rule, self.EXIT_MAP):
                # 完成局的倒计时结束后游戏会自动传出副本
                if finished and self.dungeon_wait_auto_leave(self.MAP_NAME):
                    logger.info(f'[{self.name}] completed one run (auto leave)')
                    return True
                return False

        logger.info(f'[{self.name}] completed one run')
        return True

    # ---------------------------------------------------------------- 死亡
    # 一次尝试里是否已经进过副本：死亡时用来区分「副本内死亡算完成」和「外面死亡重试」
    _entered_dungeon = False

    def handle_death(self) -> None:
        """
        阵亡：复活后回骨牢，这局按完成一次（`dungeon_left_for_death` 收尾）

        和沙狱/帝玉岭不同：那两张图里死亡不弹死亡框（直接退出副本），
        仙居福地会弹死亡框，且复活后人一定回到骨牢。
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return
        logger.warning(f'[{self.name}] 角色死亡，复活后回 {self.EXIT_MAP}')
        self.revive()
        self.handle_death_resume()

    def handle_death_resume(self) -> None:
        """
        阵亡复活后：

        - **已经进过副本**（大厅/房间里死的）：不抛异常，只等画面稳定，
          由 `dungeon_left_for_death()` 发现「已回骨牢」，按完成一次收尾（用户规则）；
        - **还没进图**（骨牢治疗/传送途中被打死）：抛 `DungeonRetry` 重试这一局，
          否则复活在城庄后 `map_teleport` 会干等到超时（真机 2026-11-16 02:01）。
        """
        self.device.sleep(1.0)
        if self._entered_dungeon:
            logger.info(f'[{self.name}] 副本内阵亡，复活后已回 {self.EXIT_MAP}，'
                        f'这一局按完成一次处理')
            return
        raise DungeonRetry(self.name)

    def dungeon_left_for_death(self) -> bool:
        """
        人是不是已经不在副本地图了（= 副本内死亡、复活回骨牢）

        基类此时会推「未通关」并抛 `DungeonAborted` 跳过该副本剩余次数；
        仙居福地按用户规则：**这局算完成一次**，交给调度器继续下一次。
        所以这里只打标，由调用方返回 True 收尾。
        """
        if getattr(self, '_died_this_run', False):
            return True
        location = self.map_current_location()
        if location is None:
            return False
        if self.map_name_match(location[0], self.MAP_NAME):
            return False
        logger.warning(f'[{self.name}] 已不在副本内（现在 {location}），'
                       f'按「副本内死亡 -> 复活回 {self.EXIT_MAP}」处理')
        self._died_this_run = True
        return True

    def dungeon_check_in_map(self, map_name: str) -> None:
        """基类会推「未通关」+ 跳过剩余次数；仙居福地的死亡要按完成一次处理"""
        self.dungeon_left_for_death()

    def finish_run_after_death(self) -> bool:
        """副本内死亡收尾：人已在骨牢，这局算完成一次（不推通知、不跳过剩余次数）"""
        logger.warning(f'[{self.name}] 副本内死亡，复活后已回到 {self.EXIT_MAP}，'
                       f'这一局按完成一次处理')
        self._died_this_run = False
        return True

    def enter_room(self, boss_name: str, room_rule_name: str,
                   room_position: tuple) -> bool:
        """点小地图房间入口，等角色走进房间到达落点坐标"""
        logger.hr(f'[{self.name}] 进入 [{boss_name}] 的房间', level=2)
        room_rule = getattr(self, f'C_{room_rule_name.upper()}')
        if self.dungeon_minimap_walk(self.ROOM_CORNER, room_rule, self.MAP_NAME,
                                     room_position,
                                     tolerance=self.ROOM_ARRIVE_TOLERANCE):
            return True
        logger.warning(f'Unable to enter room of boss [{boss_name}]')
        return False
