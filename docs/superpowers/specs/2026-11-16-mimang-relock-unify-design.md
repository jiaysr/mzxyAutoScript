# 域外迷窟 / 迷窟仙境锁定流程统一设计

**状态：** 设计已确认，待实现

**日期：** 2026-11-16

**前置：**
- `2026-11-16-wild-snowman-target-switch-design.md`（阶段 1）
- `2026-11-16-wild-snowman-relock-unify-design.md`（阶段 2，风雪人已统一）

## 背景

风雪人已经把「首次锁定 + 战斗中切换」统一成「点一次目标 → 回截图」，并验证了技能栏在非战斗/无目标状态下稳定可见。域外迷窟和迷窟仙境也是连续刷怪场景，结构几乎一致，可以迁移到同一套流程。

目前三个任务各有一份几乎相同的补锁/目标校验逻辑：

- `WildSnowman.battle_relock_once / battle_target_ok`
- `YuwaiMimang.acquire_target / relock_target / battle_target_ok`
- `MikuXianjing` 继承迷窟，并重复 `acquire_target` 调用

## 目标

- 把补锁策略上移到 `SimpleBattle`，三个任务共用。
- 迷窟 / 迷窟仙境删除首次完整锁定，首次锁定和丢失切换统一走同一套流程。
- 删除两个任务的 `fast_relock` 配置和 `acquire_target` / `relock_target` / 局部 `battle_target_ok`。
- 其他任务（Boss、副本、战车等）不变。

## 非目标

- 不修改 `battle_skill_loop`。
- 不修改 Buzhoushan / WorldBoss / Dungeon / WarChariot 等任务的锁定语义。
- 不删除 `battle_relock_target()` / `battle_on_target_lost()`（不周山等仍在用）。

## 共享层设计（`SimpleBattle`）

新增：

### `battle_target_ok(names=None, image=None)`

- `ui_target_attackable(image)` 必须为 True；
- `names` 非空时读目标名，读不出来按通过；不在白名单返回 False；
- 通过时清零 `_target_fail_count`。

语义与迷窟现有实现一致；风雪人白名单为空时从「拒绝」变成「不校验名字」，属于修正。

### `battle_relock_abort() -> bool`

默认返回 False。子类覆盖为“死亡/掉线/回位中不补锁”。

### `battle_relock_recover_position() -> bool`

默认返回 True。子类覆盖为“连续切不到目标时回打怪点”；返回 False 表示回不去。

### `battle_relock_once() -> bool`

1. `battle_relock_abort()` 为 True → False；
2. 当前帧菜单展开 → `ui_close_menu(abort_check=battle_relock_abort)`，本轮不点目标，返回 True；
3. 点一次目标（`battle_switch_target_once`）；
4. `_target_fail_count += 1`；
5. 距上次位置检查超过 `RELOCK_POSITION_CHECK_SECONDS`（60s）→ 更新计时并调用 `battle_relock_recover_position()`；失败返回 False；
6. 返回 True。

## 任务适配

### 野外雪人

- 删除自己的 `battle_relock_once` / `battle_target_ok` / `RELOCK_POSITION_CHECK_SECONDS`；
- 覆盖：
  - `battle_relock_abort()` → `self._resume_reposition`
  - `battle_relock_recover_position()` → `return_to_battle_position()`；失败置 `_resume_reposition=True` 并返回 False

### 域外迷窟

- 删除 `acquire_target` / `relock_target` / `battle_target_ok`、`TARGET_SWITCH_MAX` / `TARGET_SWITCH_TIMEOUT` / `TARGET_WARN_AFTER`、`ACTION_ATTACK` import；
- `preset_battle_loop`：
  - 不再先锁目标，直接识别技能位后进入 `battle_skill_loop`；
  - 记录 `self._battle_coord` / `self._battle_tolerance`；
  - `on_target_lost=self.battle_relock_once`；
  - 保留时段到点停止、每 6 次施法回位/修理。
- 覆盖 `battle_relock_recover_position()` → `recover_in_battle(coord, tolerance)`，失败不阻塞（返回 True）。
- `fast_relock` 配置删除，保留 `target_names`。

### 迷窟仙境

- `progress_battle_loop` 删除 `acquire_target` 调用，先识别技能位；
- 记录 `self._battle_coord` / `self._battle_tolerance`；
- `on_target_lost=self.battle_relock_once`；
- 进度 100% / 让路 / 关闭仍在每轮 `battle_stop_reason` 检查；
- 回位钩子继承迷窟。
- `fast_relock` 配置删除，保留 `target_names`。

## 失败处理

| 场景 | 行为 |
|---|---|
| 死亡 / 掉线 | 迷窟/仙境原地复活继续打；风雪人下一轮 `'reposition'` |
| 菜单展开 | 收起菜单并跳过本轮点击 |
| 连续切不到目标 | 每 60s 回一次打怪点；风雪人回不去走 reposition，迷窟/仙境继续尝试 |
| 技能位识别失败 | 迷窟：本时段收工；仙境：返回 `no_slots` |
| 时段到点 / 进度 100% / 让路 / 关闭 | 每轮停止条件优先返回 |

## 影响面

- `tasks/GameUi/navigation/battle.py`：新增共享补锁/目标校验/两个钩子。
- `tasks/LimitedTime/WildSnowman/script_task.py`：改用共享方法 + 两个钩子。
- `tasks/YuwaiMimang/script_task.py`、`config.py`：删除首次锁定与 `fast_relock`。
- `tasks/MikuXianjing/script_task.py`、`config.py`：同上。
- 测试：风雪人、迷窟、仙境相关用例更新；新增迷窟/仙境主循环不调用 `acquire_target` 的用例。

## 测试

- 共享层：菜单守卫、60s 节流、中止钩子、回位失败返回 False。
- 风雪人：两个钩子的行为（中止、回位失败置位）。
- 迷窟：`preset_battle_loop` 不再调用 `acquire_target`、技能位只识别一次、`on_target_lost` 指向共享补锁；回位钩子使用 `_battle_coord/tolerance`。
- 仙境：`progress_battle_loop` 同上，停止条件优先。
- 配置：两个任务的 `battle_config` 不再有 `fast_relock`。
- 回归：全量 `pytest tests -q` 通过。
