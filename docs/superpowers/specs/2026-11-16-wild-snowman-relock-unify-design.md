# 野外雪人锁定流程统一设计（阶段 2）

**状态：** 设计已确认，待用户审阅

**日期：** 2026-11-16

**前置：** `2026-11-16-wild-snowman-target-switch-design.md`（阶段 1：战斗中丢失目标只切一次）

## 背景

阶段 1 已经把「战斗中目标丢失」改成「点一次目标 → 回截图开头」，但首次进战仍走 `acquire_target()` / `ui_ensure_target_locked()` 完整重锁（收菜单、模板匹配按钮、最多 8 次、10s 超时）。

已确认：**技能栏在非战斗 / 无目标状态下稳定可见**，因此可以把首次锁定也并入战斗循环，删掉风雪人里的完整重锁路径。

## 目标

- 风雪人不再调用 `acquire_target()` / `ui_ensure_target_locked()`；首次锁定和战斗中切换走同一套「点一次目标 → 回截图」流程。
- 补上右上角菜单守卫，避免固定坐标点击落在展开的菜单上。
- 删除风雪人专用的 `fast_relock` 配置和不再使用的常量。
- 其他任务、`battle_skill_loop`、`ui_ensure_target_locked()` 本身保持不变。

## 非目标

- 不修改 `tasks/Battle/attack.py` 的共享循环。
- 不修改迷窟、不周山、世界首领等任务的锁定流程。
- 不删除 `SimpleBattle.ui_ensure_target_locked()` / `battle_relock_target()`（其他任务仍在用）。

## 统一后的流程

```
ensure_in_map_and_position（收菜单、走到打怪点）
 → battle_loop
     ├ 让路 / 被关闭 / 回位 / 修理（每轮判）
     ├ recognize_skill_slots()          # 不再先锁目标
     ├ slots 非空 → battle_skill_loop
     │    ├ 目标正常 → 点技能
     │    └ 目标丢失 → battle_relock_once → 回截图
     └ slots 为空 → normal_attack_until_lost
          ├ 目标正常 → 普攻
          └ 目标丢失 → battle_relock_once → 回截图
```

### `battle_loop()`

1. 删除 `acquire_target(names)` 调用及其失败处理（`sleep(1)`、`_target_fail_count % TARGET_WARN_AFTER`、回位）。
2. 保留循环开头的让路 / 关闭 / `_resume_reposition` / 修理判断。
3. 技能位识别提前到锁定之前；识别结果仍只算一次（`slots` 缓存 + `battle_current_slots()` 更新）。
4. `battle_skill_loop()` 的 `on_target_lost` 继续使用 `self.battle_relock_once`。
5. `normal_attack_until_lost()` 目标丢失时调用 `battle_relock_once()`：成功则继续下一轮，失败才返回 `'target_lost'`。

### `battle_relock_once()`

1. `_resume_reposition` 为 True：返回 False。
2. **菜单守卫**：用当前帧 `menu_icons_visible()`（`I_MENU_BAG_ICON` / `I_ACTIVITY_ICON`，不额外截图）判断：
   - 菜单展开 → `ui_close_menu(abort_check=...)`，返回 True，本轮不点目标；
   - 菜单收起 → 继续。
3. 调用 `battle_switch_target_once()` 点一次目标。
4. `_target_fail_count += 1`。
5. **位置检查时间节流**：`time.time() >= _next_relock_position_check` 时：
   - 更新下次检查时间为 `now + RELOCK_POSITION_CHECK_SECONDS`（60s）；
   - 记录 `连续 N 次没有切到白名单目标，检查打怪点位置`；
   - `return_to_battle_position()` 失败 → `_resume_reposition = True`，返回 False。
6. 返回 True。

`_next_relock_position_check` 在 `run()` 初始化为 `0.0`，第一次丢失目标时立即检查一次位置，之后每 60s 一次。`battle_target_ok()` 校验通过时把 `_target_fail_count` 清零（阶段 1 已有）。

### 位置检查为什么改成 60s

无目标时每轮约 0.1~0.2s 点一次目标。若沿用“每 10 次读一次地图坐标”，约每 2s 触发一次 `map_current_location()`（截图 + OCR，失败还要重试 3 次），开销过大。改为 60s 一次，与阶段 1 之前的实际检查频率（约 1~2 分钟）接近。

### 配置清理

- 删除 `WildSnowmanBattleConfig.fast_relock`；`WildSnowman.battle_config` 直接使用 `BattleTaskConfig`。
- 删除 `TARGET_SWITCH_MAX`、`TARGET_SWITCH_TIMEOUT`、`TARGET_WARN_AFTER`。
- `battle_relock_once` 不再判断 `battle_fast_relock()`。
- `fast_relock_help` i18n 文案保留（迷窟、不周山等任务仍在用）。

## 失败处理

| 场景 | 行为 |
|---|---|
| 死亡 / 掉线 | 截图全局处理置 `_resume_reposition`；下一轮 `battle_stop_reason` 返回 `'reposition'`，回打怪点 |
| 菜单展开 | 收起菜单并跳过本轮点击，下一轮重新判断目标 |
| 连续切不到目标 | 每 60s 检查一次打怪点位置；回不去置 `_resume_reposition` |
| 技能位识别失败 | 走普攻分支；普攻分支同样支持丢失后切目标 |
| 让路 / 任务关闭 / 修理时间到 | 每轮 `battle_stop_reason` 优先返回，不受锁定流程影响 |

## 影响面

- `tasks/LimitedTime/WildSnowman/script_task.py`：`battle_loop`、`battle_relock_once`、`battle_target_ok`、`normal_attack_until_lost`；删除 `acquire_target`；新增 `RELOCK_POSITION_CHECK_SECONDS`；常量清理。
- `tasks/LimitedTime/WildSnowman/config.py`：删除 `WildSnowmanBattleConfig`，改用 `BattleTaskConfig`。
- `tests/tasks/LimitedTime/test_wild_snowman_script.py`、`tests/tasks/LimitedTime/test_wild_snowman_config.py`：更新与新增用例。
- 共享代码与其他任务：无改动。

## 测试

- `battle_relock_once()`：
  - 菜单展开：只收菜单、不点目标、返回 True；
  - 菜单收起：点一次目标、返回 True；
  - `_resume_reposition`：不点击、返回 False；
  - 60s 时间窗内不重复检查位置；到点后检查，失败置 `_resume_reposition` 并返回 False。
- `normal_attack_until_lost()`：目标丢失时调用补锁；补锁成功继续循环，失败返回 `'target_lost'`。
- `battle_loop()`：不再调用 `acquire_target`；技能位只识别一次；有技能位走 `battle_skill_loop`，无技能位走普攻分支。
- 配置：`WildSnowman.battle_config` 不再有 `fast_relock`，其余字段默认值不变。
- 回归：`tests/tasks/LimitedTime`、`tests/tasks/GameUi/navigation`、`tests/tasks/Battle` 全通过。
