# This Python file uses the following encoding: utf-8
"""
沙狱[单人] 副本流程

入口：沙狱外 -> 小地图视口左下角 -> 点 (440,372,27,28) -> 进入「沙狱[单人]」
初始坐标：208,106
Boss：沙灵神 (109,128) -> 舞娘 (238,206)，两个都从小地图视口左上角点过去
完成：出现「你将在1分钟以后被传出副本」弹窗 -> 关闭 -> 左上角出口 (649,232,27,28)
      回到沙狱外（只判地图，不校验落点）视为完成 1 次
"""
from module.logger import logger
from tasks.Dungeon.common import DungeonBase


class ShayuSolo(DungeonBase):
    name = '沙狱[单人]'
    FIELD = 'shayu_solo'
    MAP_NAME = '沙狱[单人]'
    ENTRY_POSITION = (208, 106)
    EXIT_MAP = '沙狱外'
    # (Boss 名, 小地图点击规则, Boss 坐标)
    BOSSES = (
        ('沙灵神', 'shayu_solo_shalingshen', (109, 128)),
        ('舞娘', 'shayu_solo_wuniang', (238, 206)),
    )

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
        if not self.dungeon_minimap_walk('bottom_left', self.C_SHAYU_SOLO_ENTRY,
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
            # 上一个 Boss 的奖励弹窗可能延迟出现，先清掉再移动
            self.dungeon_close_boss_reward()
            click_rule = getattr(self, f'C_{click_name.upper()}')
            if not self.dungeon_walk_to_boss(boss_name, click_rule, self.MAP_NAME,
                                             position, 'top_left'):
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

        # 完成弹窗已出现就直接关闭；没出现就按「5 次没锁到目标」的击杀判断继续离场
        if not finished and self.dungeon_finish_detected():
            finished = True
        if finished:
            self.dungeon_close_result_popups()

        # 离开副本：小地图左上角出口 -> 沙狱外
        if not self.dungeon_minimap_walk('top_left', self.C_SHAYU_SOLO_EXIT,
                                         self.EXIT_MAP):
            # 完成/奖励弹窗挡住小地图时，先关弹窗再重试一次
            self.dungeon_close_result_popups()
            if not self.dungeon_minimap_walk('top_left', self.C_SHAYU_SOLO_EXIT,
                                             self.EXIT_MAP):
                # 完成局的倒计时结束后游戏会自动传出副本
                if finished and self.dungeon_wait_auto_leave(self.MAP_NAME):
                    logger.info(f'[{self.name}] completed one run (auto leave)')
                    return True
                return False

        logger.info(f'[{self.name}] completed one run')
        return True
