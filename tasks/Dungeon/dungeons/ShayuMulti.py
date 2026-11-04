# This Python file uses the following encoding: utf-8
"""
沙狱[多人] 副本流程

入口：沙狱外 -> 小地图视口左下角 -> 点 (479,400,28,30) -> 进入「沙狱[多人]」
初始坐标：206,107
Boss：沙灵神 (107,137) -> 舞娘 (240,205)，两个都从小地图视口左上角点过去
特殊：沙灵神击杀会弹「恭喜您成功击败沙灵神获得以下奖励」，复用 WorldBoss 的
      I_BOSS_REWARD + C_BOSS_REWARD_CLOSE 关闭
完成：出现「你将在1分钟以后被传出副本」弹窗 -> 关闭 -> 左上角出口 (649,232,27,28)
      回到沙狱外 149,197 视为完成 1 次
"""
from module.logger import logger
from tasks.Dungeon.common import DungeonBase
from tasks.WorldBoss.assets import WorldBossAssets


class ShayuMulti(DungeonBase):
    name = '沙狱[多人]'
    FIELD = 'shayu_multi'
    MAP_NAME = '沙狱[多人]'
    ENTRY_POSITION = (206, 107)
    EXIT_MAP = '沙狱外'
    EXIT_POSITION = (149, 197)
    # (Boss 名, 小地图点击规则, Boss 坐标)
    BOSSES = (
        ('沙灵神', 'shayu_multi_shalingshen', (107, 137)),
        ('舞娘', 'shayu_multi_wuniang', (240, 205)),
    )

    def dungeon_boss_reward_rule(self):
        return WorldBossAssets.I_BOSS_REWARD

    def dungeon_boss_reward_close_rule(self):
        return WorldBossAssets.C_BOSS_REWARD_CLOSE

    def run_once(self) -> bool:
        logger.hr(f'[{self.name}] 完成一次')
        # 副本前准备：外地图 + 万事通治疗 + 修理装备
        if not self.dungeon_enter_outer(self.FIELD):
            return False
        if not self.dungeon_heal():
            logger.warning('Heal failed, continue this run')
        if not self.dungeon_repair_equipment():
            logger.warning('Repair failed, continue this run')

        # 进入副本地图
        if not self.dungeon_minimap_walk('bottom_left', self.C_SHAYU_MULTI_ENTRY,
                                         self.MAP_NAME, self.ENTRY_POSITION):
            logger.warning('Unable to enter dungeon map')
            return False
        return self.run_from_inside()

    def run_from_inside(self) -> bool:
        """从副本初始坐标开始打两个 Boss、等完成弹窗并离场"""
        finished = False
        for boss_name, click_name, position in self.BOSSES:
            self.dungeon_check_in_map(self.MAP_NAME)
            if self.dungeon_finish_detected():
                logger.info('Dungeon finished before next boss')
                finished = True
                break
            click_rule = getattr(self, f'C_{click_name.upper()}')
            if not self.dungeon_minimap_walk('top_left', click_rule,
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

        # 完成弹窗已出现就直接关闭；没出现就按「5 次没锁到目标」的击杀判断继续离场
        if not finished and self.dungeon_finish_detected():
            finished = True
        if finished:
            self.dungeon_close_finish()

        # 离开副本：小地图左上角出口 -> 沙狱外 149,197
        if not self.dungeon_minimap_walk('top_left', self.C_SHAYU_MULTI_EXIT,
                                         self.EXIT_MAP, self.EXIT_POSITION):
            # 可能是完成弹窗还挡着，关掉再试一次
            if self.dungeon_finish_detected():
                self.dungeon_close_finish()
                if not self.dungeon_minimap_walk('top_left', self.C_SHAYU_MULTI_EXIT,
                                                 self.EXIT_MAP, self.EXIT_POSITION):
                    return False
            else:
                return False

        logger.info(f'[{self.name}] completed one run')
        return True
