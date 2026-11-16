# 野外雪人目标丢失切换设计

**状态：** 设计已确认，待用户审阅

**日期：** 2026-11-16

## 背景

野外雪人（`tasks/LimitedTime/WildSnowman/script_task.py`）当前通过 `SimpleBattle.battle_on_target_lost()` 走补锁流程：`battle_relock_target()` 每轮点击一次「目标」按钮后等待 0.15s、重新截图、校验「已锁定 + 可攻击 + 白名单名字」，最多切 3 次；失败后退出战斗循环，由外层 `acquire_target()` 走完整重锁（收菜单、模板匹配、最多 8 次、0.5s 间隔）。

这条链路对雪人这种「一个坐标持续刷怪、一刀一只」的场景过重：补锁内部的一张截图和一轮校验与战斗循环下一轮的截图、名字校验重复。

## 目标

- 目标丢失时只做一件事：点一次「目标」按钮切换，然后立刻回到战斗循环的截图开头。
- 切换后不等待、不截图、不校验；由下一轮循环的 `target_check` 统一校验（可攻击 + 白名单名字）。
- 改动只落在野外雪人；其他任务、`battle_skill_loop` 的公开行为不变。

## 非目标

- 不修改 `battle_skill_loop`、`battle_on_target_lost()`、`battle_relock_target()` 的既有语义。
- 不把该流程推广到迷窟、不周山、世界首领等其他战斗任务。
- 不移除 `fast_relock` 配置；关闭时仍退回原有「结束循环 -> 外层完整重锁」流程。

## 行为设计

### 触发顺序

每个循环回合保持现有顺序：

1. `screenshot()`
2. `check_stop(image)`（让路 / 任务关闭 / 修理 / 回位优先）
3. `target_check(image)`（`battle_target_ok`）
4. 目标正常 -> 算冷却 -> 点技能
5. 目标丢失 -> `battle_relock_once()`

`battle_relock_once()` 返回 True 时，`battle_skill_loop` 执行 `continue`，下一轮从 `screenshot()` 重新开始。

### `battle_relock_once()`

位于 `WildSnowman/script_task.py`：

1. `_resume_reposition` 为 True：返回 False。死亡/掉线/回位请求期间不在复活点附近点击。
2. `battle_fast_relock()` 为 False：返回 False，保持原有外层完整重锁流程。
3. 调用 `battle_switch_target_once()` 点一次「目标」按钮。
4. 累加 `_target_fail_count`。每累计 `TARGET_WARN_AFTER`（10）次仍没校验到目标：
   - 调用 `return_to_battle_position()`；
   - 回位失败则置 `_resume_reposition = True` 并返回 False，让下一轮 `check_stop` 返回 `'reposition'`。
5. 返回 True。

`battle_target_ok()` 校验通过时把 `_target_fail_count` 清零。

### `battle_switch_target_once()`

位于 `GameUi/navigation/battle.py` 的 `SimpleBattle`，作为可复用的点击机制：

- 取 `C_TARGET_BUTTON_CLICK` 固定坐标点击，`control_name='BATTLE_TARGET'`，`log=False`；
- 调用 `device.click_record_clear()`，避免被框架判定连点；
- 不 sleep、不截图、不做任何识别；返回 True。

不等待的依据：`Screenshot.screenshot()` 自带 0.1s 最小截图间隔（`module/device/screenshot.py:29`）。该间隔从上一次截图起算，极端情况下下一帧可能仍是切换前的画面，会多点一次；雪人场景最多多切一个目标，可接受。

### 名字校验

补锁阶段不读名字。下一轮 `battle_target_ok()` 会：

- 校验「已锁定 + 右下角动作按钮为攻击」；
- 读取目标名并与配置白名单（默认 `陵鱼,陵魚,可爱雪人,可愛雪人`）比对；
- 不匹配则再次判丢失并继续切换。

因此切到路过玩家时会在下一轮被发现，不会持续攻击；名字读取失败时按原目标处理，保持现有容错。

## 失败处理

| 场景 | 行为 |
|---|---|
| 死亡 / 掉线 | 截图阶段的全局处理置 `_resume_reposition`；回调返回 False；下一轮 `check_stop` 返回 `'reposition'`，外层回打怪点 |
| `fast_relock` 关闭 | 回调返回 False；退出循环后由外层 `acquire_target()` 完整重锁 |
| 连续 10 次切不到白名单目标 | 尝试摇杆/坐标回位；失败则置 `_resume_reposition` 并按 `'reposition'` 退出 |
| 附近一直没有目标 | 每轮点一次目标并重新截图；让路、任务关闭、修理计时仍在每轮 `check_stop` 生效 |

## 影响面

- `tasks/GameUi/navigation/battle.py`：新增 `battle_switch_target_once()`，不修改现有方法。
- `tasks/LimitedTime/WildSnowman/script_task.py`：新增 `battle_relock_once()`；`battle_loop` 的 `on_target_lost` 改为该回调；`battle_target_ok` 成功时清零失败计数。
- 其他任务与共享战斗循环：无行为变化。

## 测试

- `battle_relock_once()`：
  - 正常：点一次目标、返回 True、不 sleep/不截图；
  - `_resume_reposition` 为 True：不点击、返回 False；
  - `fast_relock` 关闭：不点击、返回 False；
  - 连续 10 次：调用回位；回位失败置 `_resume_reposition` 并返回 False。
- `battle_target_ok()` 成功时清零 `_target_fail_count`。
- `test_battle_loop_recognizes_skill_slots_only_once()` 的假任务改用 `battle_relock_once`。
- 原有 `battle_on_target_lost` / `battle_relock_target` 测试保留（其他任务仍在使用）。
