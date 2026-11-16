# This Python file uses the following encoding: utf-8
"""
副本玩法（Dungeon）：一个任务里跑多个不同的副本。

结构：
- 通用逻辑：tasks/Dungeon/common.py（DungeonBase）
- 具体副本：tasks/Dungeon/dungeons/ 下每个副本一个文件，继承 DungeonBase
- 入口：本文件 ScriptTask 按配置挑出启用的副本，逐个执行

新增一个副本：
1. 在 tasks/Dungeon/dungeons/ 下新建 <Name>.py（继承 DungeonBase，实现 run_once()）
2. 在 tasks/Dungeon/config.py 的 DungeonConfig 里加该副本的开关/参数
3. 在下面 DUNGEONS 登记：DungeonConfig 里的开关字段名 -> 副本实现类
"""
from module.exception import TaskEnd
from module.logger import logger
from tasks.Dungeon.common import DungeonBase, DungeonRetry
from tasks.Dungeon.dungeons.DiyulingMulti import DiyulingMulti
from tasks.Dungeon.dungeons.DiyulingSolo import DiyulingSolo
from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing
from tasks.Dungeon.dungeons.ShayuMulti import ShayuMulti
from tasks.Dungeon.dungeons.ShayuSolo import ShayuSolo
from tasks.Dungeon.dungeons.XianjuFudi import XianjuFudi
from tasks.Dungeon.runner import DungeonAborted

# 副本登记表：DungeonConfig 里的开关字段名 -> 副本实现类
DUNGEONS: dict = {
    'shayu_multi_enable': ShayuMulti,
    'shayu_solo_enable': ShayuSolo,
    'diyuling_multi_enable': DiyulingMulti,
    'diyuling_solo_enable': DiyulingSolo,
    'liuli_huanjing_enable': LiuliHuanjing,
    'xianju_fudi_enable': XianjuFudi,
}


class ScriptTask(DungeonBase):
    """副本玩法入口：按配置挑出启用的副本逐个执行"""

    name = '副本玩法'
    # 副本外阵亡（传送/寻路途中）重试当前这一局的次数
    DEATH_RETRY = 3

    def run(self) -> None:
        logger.hr('副本玩法')
        try:
            cfg = self.config.dungeon.dungeon_config
            enabled = self.enabled_dungeons(cfg)
            if not enabled:
                logger.warning('没有启用任何副本（DUNGEONS 为空或都未勾选），本次跳过')
            else:
                logger.attr('启用副本', ', '.join(getattr(dungeon_cls, 'name', field)
                                                 for field, dungeon_cls in enabled))
                self.prepare()
                self.dungeon_run_all(enabled)
        finally:
            # 正常/异常都重排，避免 next_run 停在过去霸占调度；
            # 死亡重跑时 rerun_task 已置 _rerun_requested，这里会被忽略
            self.set_next_run(task='Dungeon', success=True, finish=True)
        raise TaskEnd('Dungeon')

    def dungeon_run_all(self, enabled: list) -> None:
        """按配置次数 + 重置/特权规则执行所有副本"""
        from tasks.Dungeon.counts import FIELD_NAMES
        from tasks.Dungeon.runner import DungeonRunner

        cfg = self.config.dungeon.dungeon_config
        targets = [{
            'field': field,
            'name': FIELD_NAMES.get(field, field),
            'configured': int(getattr(cfg, f'{field}_count', 1)),
        } for field, _ in enabled]
        runner = DungeonRunner(
            targets=targets,
            privilege_field=self.privilege_dungeon_field(),
            status=self.dungeon_status,
            select=self.dungeon_select,
            reset=self.dungeon_click_reset,
            run_once=self.dungeon_run_once,
            field_status=self.dungeon_field_status,
        )
        runner.run()

    def dungeon_run_once(self, field: str) -> bool:
        """
        派发到具体副本类完成一次挑战

        副本**外**阵亡（传送/寻路/治疗/修理途中）复活后重试当前这一局，
        连续 `DEATH_RETRY` 次都站不住脚才跳过这个副本的剩余次数（不影响其它副本）。
        """
        cfg = self.config.dungeon.dungeon_config
        dungeon_cls = dict(self.enabled_dungeons(cfg)).get(field)
        if dungeon_cls is None:
            raise RuntimeError(f'Dungeon class for [{field}] is not registered')
        name = getattr(dungeon_cls, 'name', field)
        for attempt in range(1, self.DEATH_RETRY + 1):
            try:
                logger.hr(f'副本：{name}')
                result = bool(dungeon_cls(self.config, self.device).run_once())
                self.device.sleep(int(cfg.dungeon_interval))
                return result
            except DungeonRetry as e:
                logger.warning(f'[{name}] 副本外阵亡，重试当前这一局 '
                               f'（{attempt}/{self.DEATH_RETRY}）：{e}')
                self.device.sleep(2)
        logger.warning(f'[{name}] 连续 {self.DEATH_RETRY} 次在副本外阵亡，'
                       f'跳过它剩余的次数，继续其它副本')
        raise DungeonAborted(f'{name}: died outside dungeon {self.DEATH_RETRY} times')

    def enabled_dungeons(self, cfg) -> list:
        """
        按 DUNGEONS 登记表挑出配置里勾选的副本
        :return: [(配置字段前缀, 副本实现类), ...]
                 例如 shayu_multi_enable -> shayu_multi
        """
        result = []
        for enable_field, dungeon_cls in DUNGEONS.items():
            if not getattr(cfg, enable_field, False):
                continue
            field = (enable_field[:-len('_enable')]
                     if enable_field.endswith('_enable') else enable_field)
            result.append((field, dungeon_cls))
        return result


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    t = ScriptTask(config, device)
    t.device.click(626,385)  # 先点一下，避免被悬浮窗挡住
