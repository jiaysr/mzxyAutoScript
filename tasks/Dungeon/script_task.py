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

# 副本登记表：DungeonConfig 里的开关字段名 -> 副本实现类
# 有具体副本后在 config.py 加开关、在这里登记，例如：
#   'dungeon_xxx_enable': XxxDungeon,
DUNGEONS: dict = {}


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
                for field, dungeon_cls in enabled:
                    logger.hr(f'副本：{getattr(dungeon_cls, "name", field)}')
                    dungeon_cls(self.config, self.device).run()
                    self.device.sleep(int(cfg.dungeon_interval))
        finally:
            # 正常/异常都重排，避免 next_run 停在过去霸占调度；
            # 死亡重跑时 rerun_task 已置 _rerun_requested，这里会被忽略
            self.set_next_run(task='Dungeon', success=True, finish=True)
        raise TaskEnd('Dungeon')

    def enabled_dungeons(self, cfg) -> list:
        """
        按 DUNGEONS 登记表挑出配置里勾选的副本
        :return: [(字段名, 副本实现类), ...]
        """
        result = []
        for field, dungeon_cls in DUNGEONS.items():
            if getattr(cfg, field, False):
                result.append((field, dungeon_cls))
        return result


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
