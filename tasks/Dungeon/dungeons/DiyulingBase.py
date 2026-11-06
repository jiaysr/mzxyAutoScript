# This Python file uses the following encoding: utf-8
"""
帝玉岭副本通用流程（单人 / 多人）

所有小地图操作都在「视口左下角」：
- 帝玉岭外：万事通 / 副本入口
- 副本内：两个 Boss / 出口

单人/多人只有入口点击不同，内部点位当前按同一套坐标处理；出口只判外地图。
击杀后掉落贵重物品会弹领奖弹窗，由基类统一用 WorldBoss 的 I_BOSS_REWARD 关闭。
"""
from module.logger import logger
from tasks.Dungeon.common import DungeonBase


class DiyulingBase(DungeonBase):
    ENTRY_CLICK_NAME = ''
    MAP_NAME = ''
    ENTRY_POSITION = None
    EXIT_MAP = '帝玉岭外'
    EXIT_CLICK_NAME = 'diyuling_exit'  # 单/多人共用
    # (Boss 名, 小地图点击规则, Boss 坐标)
    BOSSES = ()
    # 「沙王」只有两个字：OCR 会把「王」认成「主」「玉」（2026-11-05 真机实测 0.73~0.78，
    # 都高于置信度门槛所以不会被丢弃），而 ocr_name_match 对 2 字名称没有形近字容错
    # （len(name) < 3 直接判不匹配），读错一次就永远锁不上 -> 反复换目标 -> 最后 assume killed。
    # 这里把实测到的两种误读登记成别名，_dungeon_target_names() 会把它们展开进白名单。
    BOSS_NAME_ALIASES = {'沙王': ('沙主', '沙玉')}

    def run_once(self) -> bool:
        logger.hr(f'[{self.name}] 完成一次')
        if not self.dungeon_enter_outer(self.FIELD):
            return False
        if not self.dungeon_heal():
            logger.warning('Heal failed, continue this run')
        if not self.dungeon_repair_equipment():
            logger.warning('Repair failed, continue this run')

        entry_rule = getattr(self, f'C_{self.ENTRY_CLICK_NAME.upper()}')
        if not self.dungeon_minimap_walk('bottom_left', entry_rule,
                                         self.MAP_NAME, self.ENTRY_POSITION):
            logger.warning('Unable to enter dungeon map')
            return False
        return self.run_from_inside()

    def run_from_inside(self) -> bool:
        """从副本初始坐标打两个 Boss、处理完成弹窗并离场"""
        finished = False
        for boss_name, click_name, position in self.BOSSES:
            self.dungeon_check_in_map(self.MAP_NAME)
            if self.dungeon_finish_detected():
                logger.info('Dungeon finished before next boss')
                finished = True
                break
            # 上一个 Boss 的奖励弹窗可能延迟出现，先清掉再移动
            self.dungeon_close_boss_reward()
            click_rule = getattr(self, f'C_{click_name.upper()}')
            if not self.dungeon_walk_to_boss(boss_name, click_rule, self.MAP_NAME,
                                             position, 'bottom_left'):
                logger.warning(f'Unable to walk to boss [{boss_name}]')
                return False
            result = self.dungeon_fight_boss(boss_name, map_name=self.MAP_NAME)
            self.dungeon_check_in_map(self.MAP_NAME)
            logger.info(f'Boss [{boss_name}] result: {result}')
            if result == 'finished':
                finished = True
                break
            if result != 'killed':
                logger.warning(f'Boss [{boss_name}] fight failed: {result}')
                return False

        # 完成弹窗出现就直接关闭；没出现就按 5 次没锁到目标的击杀判断继续离场
        if not finished and self.dungeon_finish_detected():
            finished = True
        if finished:
            self.dungeon_close_result_popups()

        exit_rule = getattr(self, f'C_{self.EXIT_CLICK_NAME.upper()}')
        if not self.dungeon_minimap_walk('bottom_left', exit_rule, self.EXIT_MAP):
            # 完成/奖励弹窗挡住小地图时，先关弹窗再重试一次
            self.dungeon_close_result_popups()
            if not self.dungeon_minimap_walk('bottom_left', exit_rule, self.EXIT_MAP):
                # 完成局的倒计时结束后游戏会自动传出副本
                if finished and self.dungeon_wait_auto_leave(self.MAP_NAME):
                    logger.info(f'[{self.name}] completed one run (auto leave)')
                    return True
                return False

        logger.info(f'[{self.name}] completed one run')
        return True
