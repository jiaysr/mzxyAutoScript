# This Python file uses the following encoding: utf-8
"""
副本挑战状态机（纯逻辑，不连设备）

负责「正常次数 -> 重置提交 -> 特权额外次数」的调度，具体动作通过回调注入：

- status()    读取 {"counts": {字段: 剩余次数}, "privilege": 特权剩余次数}
- select(name) 点击左侧列表选中副本（只在重置提交前调用）
- reset()      点击右侧固定位置的「重置」按钮（前提：目标已选中）
- run_once(field) 完成一次副本挑战（由具体副本实现，从地图入口进入）

规则（真机行为）：
1. 打 2 次及以上的副本，跑完一次后列表次数可能不会自动 -1；
   不重置怪物也不会刷新，所以要再点一次「重置」把次数提交并刷新。
2. 正常次数用完后才有特权次数：先在目标副本上把剩余正常次数重置到 0，
   再打特权；打完特权次数没 -1 也要重置提交，直到特权次数为 0。
3. 特权副本处理完后继续按配置顺序打其它副本。
4. 开局/跑完的次数都只 OCR 列表行，不点击；点击列表只在重置前选行。
"""
from module.logger import logger


class DungeonAborted(Exception):
    """副本中死亡/自动离开：跳过该副本剩余次数，继续其它副本"""
    pass


class DungeonRunner:
    def __init__(self, targets: list, privilege_field: str, status, select,
                 reset, run_once, max_steps: int = 60, field_status=None):
        """
        :param targets: [{'field': 配置字段, 'name': 列表副本名, 'configured': 配置次数}, ...]
        :param privilege_field: 特权副本字段；None 表示不使用特权
        :param status: () -> {'counts': {field: int}, 'privilege': int|None}
        :param select: (name: str) -> bool
        :param reset: () -> bool（目标已选中后点重置）
        :param run_once: (field: str) -> bool（完成一次副本）
        :param max_steps: 特权阶段最大循环次数（防异常死循环）
        :param field_status: (field) -> (count, privilege)；跑完只识别当前副本，
                             不传则回退到 status() 全量识别
        """
        self.targets = targets
        self.privilege_field = privilege_field
        self.status = status
        self.select = select
        self.reset = reset
        self.run_once = run_once
        self.max_steps = max_steps
        self.field_status = field_status
        self._aborted = set()

    def run(self) -> tuple:
        counts, privilege = self._read_status()
        for target in self.targets:
            counts, privilege = self._run_normal(target, counts, privilege)
            if target['field'] in self._aborted:
                continue
            if target['field'] == self.privilege_field:
                counts, privilege = self._run_privilege(target, counts, privilege)
        logger.attr('Dungeon final counts', counts)
        logger.attr('Dungeon final privilege', privilege)
        return counts, privilege

    # ---------------------------------------------------------------- 内部
    def _read_status(self) -> tuple:
        data = self.status() or {}
        return dict(data.get('counts') or {}), data.get('privilege')

    def _read_field(self, field: str, counts: dict, privilege) -> tuple:
        """跑完/重置后只读当前副本的次数；没有 field_status 时回退全量识别"""
        if self.field_status is None:
            new_counts, privilege = self._read_status()
            merged = dict(counts)
            merged.update(new_counts)
            return merged, privilege
        count, privilege = self.field_status(field)
        merged = dict(counts)
        if count is not None:
            merged[field] = count
        return merged, privilege

    def _select(self, target: dict) -> None:
        if not self.select(target['name']):
            raise RuntimeError(f"Unable to select dungeon [{target['name']}]")

    def _reset(self, target: dict, counts: dict, privilege) -> tuple:
        self._select(target)
        if not self.reset():
            raise RuntimeError(f"Reset failed for dungeon [{target['name']}]")
        return self._read_field(target['field'], counts, privilege)

    def _run_normal(self, target: dict, counts: dict, privilege) -> tuple:
        field = target['field']
        attempts = min(int(target['configured']), counts.get(field, 0))
        for index in range(attempts):
            if counts.get(field, 0) <= 0:
                break
            before = counts.get(field, 0)
            # 跑副本从地图入口进，不依赖副本页选中；选中只在重置时进行
            try:
                ok = self.run_once(field)
            except DungeonAborted as e:
                logger.warning(f'Dungeon [{target["name"]}] aborted: {e}, '
                               f'skip remaining attempts')
                self._aborted.add(field)
                return counts, privilege
            if not ok:
                raise RuntimeError(f"Run failed for dungeon [{target['name']}]")
            counts, privilege = self._read_field(field, counts, privilege)
            after = counts.get(field, 0)

            # 2 次及以上的副本：跑完一次后次数没自动 -1，需要重置提交/刷新怪物
            if attempts >= 2 and after >= before:
                logger.info(f'[{target["name"]}] count {before} -> {after}, reset to commit')
                counts, privilege = self._reset(target, counts, privilege)
                after = counts.get(field, 0)
                if after >= before:
                    logger.warning(f'[{target["name"]}] reset did not decrease count, retry once')
                    counts, privilege = self._reset(target, counts, privilege)
                    after = counts.get(field, 0)
                if after >= before:
                    raise RuntimeError(
                        f"Reset did not decrease [{target['name']}] count ({before} -> {after})")
            elif after < before:
                logger.info(f'[{target["name"]}] count decreased normally '
                            f'{before} -> {after}, no reset')
            else:
                logger.warning(f'[{target["name"]}] count did not decrease '
                               f'({before} -> {after}), attempts={attempts}, leave it')
            logger.info(f'[{target["name"]}] normal run {index + 1}/{attempts}: '
                        f'{before} -> {after}')
        return counts, privilege

    def _run_privilege(self, target: dict, counts: dict, privilege) -> tuple:
        field = target['field']
        if privilege is None:
            logger.warning('Privilege count is unreadable, skip privilege phase')
            return counts, privilege

        steps = 0
        while privilege > 0 and steps < self.max_steps:
            counts, privilege = self._read_field(field, counts, privilege)
            if privilege is None:
                logger.warning('Privilege count became unreadable, stop')
                break
            if privilege <= 0:
                break

            # 正常次数没清零时先重置（每次重置提交 1 次并刷新怪物）
            if counts.get(field, 0) != 0:
                before_normal = counts.get(field, 0)
                logger.info(f'[{target["name"]}] normal count '
                            f'{before_normal} != 0, reset before privilege')
                counts, privilege = self._reset(target, counts, privilege)
                if counts.get(field, 0) >= before_normal:
                    logger.warning(f'[{target["name"]}] reset did not decrease count, retry once')
                    counts, privilege = self._reset(target, counts, privilege)
                if counts.get(field, 0) >= before_normal:
                    raise RuntimeError(
                        f"Reset did not decrease [{target['name']}] count "
                        f"({before_normal} -> {counts.get(field, 0)})")
                steps += 1
                continue

            before = privilege
            # 跑副本不需要先在副本页选中，只有重置提交才需要
            try:
                ok = self.run_once(field)
            except DungeonAborted as e:
                logger.warning(f'Privilege phase of [{target["name"]}] aborted: {e}')
                self._aborted.add(field)
                return counts, privilege
            if not ok:
                raise RuntimeError(f"Privilege run failed for dungeon [{target['name']}]")
            counts, privilege = self._read_field(field, counts, privilege)
            if privilege is None:
                logger.warning('Privilege count unreadable after run, stop')
                break

            if privilege >= before:
                logger.info(f'Privilege {before} -> {privilege} after run, reset to commit')
                counts, privilege = self._reset(target, counts, privilege)
                if privilege is not None and privilege >= before:
                    logger.warning('Privilege still not decreased, reset retry once')
                    counts, privilege = self._reset(target, counts, privilege)
                if privilege is not None and privilege >= before:
                    raise RuntimeError(
                        f'Privilege count did not decrease after reset: {before} -> {privilege}')
            logger.info(f'[{target["name"]}] privilege run: {before} -> {privilege}')
            steps += 1

        if privilege and privilege > 0:
            logger.warning(f'Privilege phase not finished, remaining {privilege}')
        return counts, privilege
