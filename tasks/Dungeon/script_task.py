# This Python file uses the following encoding: utf-8
"""
副本玩法（Dungeon）：一个任务里跑多个不同的副本。

结构：
- 通用逻辑：tasks/Dungeon/common.py（DungeonBase）
- 具体副本：tasks/Dungeon/dungeons/ 下每个副本一个文件，继承 DungeonBase
- 入口：本文件 ScriptTask 按配置挑出启用的副本，逐个执行

新增一个副本：
1. 在 tasks/Dungeon/dungeons/ 下新建 <Name>.py（继承 DungeonBase，实现 run()）
2. 在 tasks/Dungeon/config.py 的 DungeonConfig 里加该副本的开关/参数
3. 在下面 DUNGEONS 登记：DungeonConfig 里的开关字段名 -> 副本实现类
"""
from module.exception import TaskEnd
from module.logger import logger
from tasks.Dungeon.common import DungeonBase
from tasks.Dungeon.dungeons.DiyulingMulti import DiyulingMulti
from tasks.Dungeon.dungeons.DiyulingSolo import DiyulingSolo
from tasks.Dungeon.dungeons.LiuliHuanjing import LiuliHuanjing
from tasks.Dungeon.dungeons.ShayuMulti import ShayuMulti
from tasks.Dungeon.dungeons.ShayuSolo import ShayuSolo

# 副本登记表：DungeonConfig 里的开关字段名 -> 副本实现类
DUNGEONS: dict = {
    'shayu_multi_enable': ShayuMulti,
    'shayu_solo_enable': ShayuSolo,
    'diyuling_multi_enable': DiyulingMulti,
    'diyuling_solo_enable': DiyulingSolo,
    'liuli_huanjing_enable': LiuliHuanjing,
}


class ScriptTask(DungeonBase):
    """副本玩法入口：按配置挑出启用的副本逐个执行"""

    name = '副本玩法'

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
        """派发到具体副本类完成一次挑战"""
        cfg = self.config.dungeon.dungeon_config
        dungeon_cls = dict(self.enabled_dungeons(cfg)).get(field)
        if dungeon_cls is None:
            raise RuntimeError(f'Dungeon class for [{field}] is not registered')
        logger.hr(f'副本：{getattr(dungeon_cls, "name", field)}')
        result = bool(dungeon_cls(self.config, self.device).run_once())
        self.device.sleep(int(cfg.dungeon_interval))
        return result

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
    t.device.click(661,176)  # 先点一下，避免被悬浮窗挡住
