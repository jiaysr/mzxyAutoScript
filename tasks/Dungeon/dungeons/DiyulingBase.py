# This Python file uses the following encoding: utf-8
"""
帝玉岭副本通用流程（单人 / 多人）

所有小地图操作都在「视口左下角」：
- 帝玉岭外：万事通 / 副本入口
- 副本内：两个 Boss / 出口

单人/多人只有入口点击和出口到达坐标不同，内部点位当前按同一套坐标处理。
"""
from module.logger import logger
from tasks.Dungeon.common import DungeonBase
from tasks.WorldBoss.assets import WorldBossAssets


class DiyulingBase(DungeonBase):
    ENTRY_CLICK_NAME = ''
    MAP_NAME = ''
    ENTRY_POSITION = None
    EXIT_MAP = '帝玉岭外'
    EXIT_CLICK_NAME = 'diyuling_solo_exit'  # 单/多人共用
    EXIT_POSITION = None
    # (Boss 名, 小地图点击规则, Boss 坐标)
    BOSSES = ()

    def dungeon_boss_reward_rule(self):
        # 幽灵有领奖弹窗：复用世界首领的归属奖检测/关闭
        return WorldBossAssets.I_BOSS_REWARD

    def dungeon_boss_reward_close_rule(self):
        return WorldBossAssets.C_BOSS_REWARD_CLOSE

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
            click_rule = getattr(self, f'C_{click_name.upper()}')
            if not self.dungeon_minimap_walk('bottom_left', click_rule,
                                             self.MAP_NAME, position,
                                             abort_on_leave=True):
                logger.warning(f'Unable to walk to boss [{boss_name}]')
                return False
            result = self.dungeon_fight_boss(boss_name)
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
            self.dungeon_close_finish()

        exit_rule = getattr(self, f'C_{self.EXIT_CLICK_NAME.upper()}')
        if not self.dungeon_minimap_walk('bottom_left', exit_rule,
                                         self.EXIT_MAP, self.EXIT_POSITION):
            if self.dungeon_finish_detected():
                self.dungeon_close_finish()
                if not self.dungeon_minimap_walk('bottom_left', exit_rule,
                                                 self.EXIT_MAP, self.EXIT_POSITION):
                    return False
            else:
                return False

        logger.info(f'[{self.name}] completed one run')
        return True
