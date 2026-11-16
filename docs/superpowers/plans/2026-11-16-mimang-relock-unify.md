# 域外迷窟 / 迷窟仙境锁定流程统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把补锁策略上移到 `SimpleBattle`，迷窟 / 迷窟仙境删除首次完整锁定与 `fast_relock`，统一走「点一次目标 → 回截图」。

**Architecture:** `SimpleBattle` 提供 `battle_relock_once` / `battle_target_ok` / 两个钩子；风雪人、迷窟、仙境各自只覆盖中止与回位钩子。三个任务的战斗循环不再调用 `acquire_target`。

**Tech Stack:** Python 3.10、pytest（`toolkit\python.exe -m pytest`）、现有 `GameUi` / `BattleAttack` / 设备控制栈。

**Spec:** `docs/superpowers/specs/2026-11-16-mimang-relock-unify-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `tasks/GameUi/navigation/battle.py` | 公共战斗能力 | 新增 `battle_target_ok` / `battle_relock_once` / 两个钩子 / 60s 常量 |
| `tasks/LimitedTime/WildSnowman/script_task.py` | 风雪人 | 删除自己的补锁/目标校验，改用钩子 |
| `tasks/YuwaiMimang/script_task.py` | 域外迷窟 | 删除首次锁定、局部补锁/目标校验，接入共享方法 |
| `tasks/YuwaiMimang/config.py` | 迷窟配置 | 删除 `fast_relock` |
| `tasks/MikuXianjing/script_task.py` | 迷窟仙境 | 删除首次锁定，接入共享方法 |
| `tasks/MikuXianjing/config.py` | 仙境配置 | 删除 `fast_relock` |
| `tests/...` | 测试 | 更新风雪人、仙境配置；新增迷窟配置/主循环用例 |

---

### Task 1: 共享层 + 风雪人改用钩子

**Files:**
- Modify: `tasks/GameUi/navigation/battle.py`（import、类常量、`battle_switch_target_once` 之后）
- Modify: `tasks/LimitedTime/WildSnowman/script_task.py:49-60`、`:312-355`
- Test: `tests/tasks/LimitedTime/test_wild_snowman_script.py:181-270`

- [ ] **Step 1: 写失败测试**

把 `tests/tasks/LimitedTime/test_wild_snowman_script.py` 里 6 个 `battle_relock_once...` 用例的假任务改成使用新钩子（把 `_resume_reposition` / `return_to_battle_position` 换成 `battle_relock_abort` / `battle_relock_recover_position`），并在最后一个用例后追加两个钩子用例：

```python
def test_battle_relock_once_clicks_once_without_waiting():
    clicks = []
    task = SimpleNamespace(
        battle_relock_abort=lambda: False,
        battle_relock_recover_position=lambda: True,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: clicks.append('click') or True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert clicks == ['click']
    assert task._target_fail_count == 1


def test_battle_relock_once_aborts_on_reposition():
    clicks = []
    task = SimpleNamespace(
        battle_relock_abort=lambda: True,
        battle_relock_recover_position=lambda: True,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: clicks.append('click') or True,
    )
    assert ScriptTask.battle_relock_once(task) is False
    assert clicks == []


def test_battle_relock_once_closes_menu_before_switching():
    menu = []
    clicks = []
    task = SimpleNamespace(
        battle_relock_abort=lambda: False,
        battle_relock_recover_position=lambda: True,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: True,
        ui_close_menu=lambda abort_check=None: menu.append('close') or True,
        battle_switch_target_once=lambda: clicks.append('click') or True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert menu == ['close']
    assert clicks == []


def test_battle_relock_once_checks_position_after_interval():
    repositions = []
    task = SimpleNamespace(
        battle_relock_abort=lambda: False,
        battle_relock_recover_position=lambda: repositions.append('back') or True,
        _target_fail_count=0,
        _next_relock_position_check=0.0,
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert repositions == ['back']
    assert task._next_relock_position_check > 0


def test_battle_relock_once_skips_position_check_within_interval():
    repositions = []
    task = SimpleNamespace(
        battle_relock_abort=lambda: False,
        battle_relock_recover_position=lambda: repositions.append('back') or True,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert repositions == []


def test_battle_relock_once_stops_when_recover_fails():
    task = SimpleNamespace(
        battle_relock_abort=lambda: False,
        battle_relock_recover_position=lambda: False,
        _target_fail_count=0,
        _next_relock_position_check=0.0,
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is False


def test_battle_relock_recover_position_sets_flag_on_failure():
    task = SimpleNamespace(_resume_reposition=False,
                           return_to_battle_position=lambda: False)
    assert ScriptTask.battle_relock_recover_position(task) is False
    assert task._resume_reposition is True


def test_battle_relock_recover_position_keeps_flag_on_success():
    task = SimpleNamespace(_resume_reposition=False,
                           return_to_battle_position=lambda: True)
    assert ScriptTask.battle_relock_recover_position(task) is True
    assert task._resume_reposition is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q -k "battle_relock"
```

预期：`FAILED`，`battle_relock_abort` / `battle_relock_recover_position` 不存在。

- [ ] **Step 3: 添加共享方法**

在 `tasks/GameUi/navigation/battle.py` 顶部 `from typing import Callable` 之前加：

```python
import time
```

在 `SimpleBattle` 类常量区（`RELOCK_SETTLE` 附近）加：

```python
    # 目标丢失补锁：连续切不到目标时，多久检查一次打怪点位置（秒）
    RELOCK_POSITION_CHECK_SECONDS = 60
```

在 `battle_switch_target_once()` 之后、`battle_on_target_lost()` 之前插入：

```python
    def battle_target_ok(self, names=None, image=None) -> bool:
        """
        放技能前确认「锁着的就是白名单目标」，避免打到路过的玩家

        每次现查名字（不缓存）：玩家可攻击，只判「可攻击」会在怪死后 /
        游戏自动转锁到玩家时继续打偏。名字读不出来时按原目标处理；
        白名单为空 = 不校验名字。校验通过时清零补锁失败计数。
        """
        if not self.ui_target_attackable(image=image):
            return False
        if names:
            name = self.ui_target_name()
            if name and not any(self.ocr_name_match(name, expected) for expected in names):
                logger.warning(f'锁着的目标变成 [{name}]，不在白名单 {names}，停止放技能')
                return False
        self._target_fail_count = 0
        return True

    def battle_relock_abort(self) -> bool:
        """目标丢失补锁是否应中止（死亡/掉线/回位中）；任务按需覆盖"""
        return False

    def battle_relock_recover_position(self) -> bool:
        """连续切不到目标时回打怪点；返回 False 表示回不去；任务按需覆盖"""
        return True

    def battle_relock_once(self) -> bool:
        """
        目标丢失：点一次「目标」按钮切换，然后立刻回战斗循环的截图开头

        补锁内部不等待、不截图、不校验，把「是不是可攻击 + 名字对不对」
        留给下一轮 `battle_target_ok`。菜单展开时目标按钮会被盖住，先收起菜单，
        本轮不点目标。

        无目标时会连续点很多次，位置检查按 `RELOCK_POSITION_CHECK_SECONDS`
        节流：每 60s 调一次 `battle_relock_recover_position`。
        """
        if self.battle_relock_abort():
            return False
        if self.menu_icons_visible():
            self.ui_close_menu(abort_check=self.battle_relock_abort)
            return True
        self.battle_switch_target_once()
        self._target_fail_count = getattr(self, '_target_fail_count', 0) + 1
        now = time.time()
        if now >= getattr(self, '_next_relock_position_check', 0.0):
            self._next_relock_position_check = now + self.RELOCK_POSITION_CHECK_SECONDS
            logger.warning(f'连续 {self._target_fail_count} 次没有切到白名单目标，'
                           f'检查打怪点位置')
            if not self.battle_relock_recover_position():
                return False
        return True
```

- [ ] **Step 4: 风雪人改用钩子**

在 `tasks/LimitedTime/WildSnowman/script_task.py`：

1. 删除类常量 `RELOCK_POSITION_CHECK_SECONDS = 60`（已上移）。
2. 删除整个 `battle_target_ok()` 和 `battle_relock_once()` 方法。
3. 在 `# ---- 锁目标 / 技能` 注释后加入：

```python
    def battle_relock_abort(self) -> bool:
        """死亡 / 掉线 / 回位中不补锁"""
        return self._resume_reposition

    def battle_relock_recover_position(self) -> bool:
        """连续切不到目标时回打怪点；回不去让停止条件走 'reposition'"""
        if self.return_to_battle_position():
            return True
        self._resume_reposition = True
        return False
```

- [ ] **Step 5: 运行测试确认通过**

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py tests/tasks/GameUi/navigation/test_battle_logging.py -q
```

预期：全部 `passed`。

- [ ] **Step 6: 提交**

```bash
git add tasks/GameUi/navigation/battle.py tasks/LimitedTime/WildSnowman/script_task.py tests/tasks/LimitedTime/test_wild_snowman_script.py
git commit -m "refactor(battle): 补锁策略上移到 SimpleBattle"
```

---

### Task 2: 域外迷窟迁移

**Files:**
- Modify: `tasks/YuwaiMimang/script_task.py:14-16`、`:535-608`、`:717-791`
- Modify: `tasks/YuwaiMimang/config.py:9-28`
- Test: 新建 `tests/tasks/YuwaiMimang/test_battle_loop.py`、新建 `tests/tasks/YuwaiMimang/test_config.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/tasks/YuwaiMimang/test_config.py`：

```python
# -*- coding: utf-8 -*-
"""域外迷窟 配置测试（不连设备）"""
from tasks.YuwaiMimang.config import YuwaiMimang


def test_battle_config_has_no_fast_relock():
    cfg = YuwaiMimang()
    assert cfg.battle_config.target_names == '小鬼,鬼王,鬼将'
    assert not hasattr(cfg.battle_config, 'fast_relock')
```

新建 `tests/tasks/YuwaiMimang/test_battle_loop.py`：

```python
# -*- coding: utf-8 -*-
"""域外迷窟 打怪主循环测试（不连设备）"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tasks.YuwaiMimang.script_task import ScriptTask


class _StopLoop(Exception):
    pass


def test_preset_battle_loop_no_acquire_and_uses_relock():
    loops = []
    recognized = []

    def battle_skill_loop(*args, **kwargs):
        loops.append(kwargs)
        if len(loops) > 2:
            raise _StopLoop
        return 'timeout'

    task = SimpleNamespace(
        battle_target_names=lambda: ['小鬼'],
        battle_use_normal_attack=lambda: False,
        battle_preset=lambda: {'name': '默认预设'},
        reset_battle_progress=lambda end: None,
        recognize_skill_slots=lambda: recognized.append('slots') or [0, 1],
        battle_current_slots=lambda: [0, 1],
        battle_skill_loop=battle_skill_loop,
        battle_stop_reason=lambda image=None: None,
        battle_target_ok=lambda names, image=None: True,
        battle_relock_once=lambda: False,
        NORMAL_ATTACK_INTERVAL=1.0,
        RECOVER_EVERY_CASTS=6,
        device=SimpleNamespace(sleep=lambda seconds: None),
        recover_in_battle=lambda coord, tolerance: True,
        need_repair=lambda cfg, last_repair, end: False,
    )

    with pytest.raises(_StopLoop):
        ScriptTask.preset_battle_loop(
            task, (304, 76), 2, datetime.now() + timedelta(minutes=1), ['小鬼'], None)

    assert len(recognized) == 1
    assert len(loops) == 3
    assert loops[0]['on_target_lost'] is task.battle_relock_once
    assert task._battle_coord == (304, 76)
    assert task._battle_tolerance == 2


def test_relock_recover_uses_battle_coord():
    calls = []
    task = SimpleNamespace(
        _battle_coord=(304, 76),
        _battle_tolerance=2,
        recover_in_battle=lambda coord, tolerance: calls.append((coord, tolerance)) or True,
    )
    assert ScriptTask.battle_relock_recover_position(task) is True
    assert calls == [((304, 76), 2)]


def test_relock_recover_never_blocks_grinding():
    task = SimpleNamespace(
        _battle_coord=(304, 76),
        _battle_tolerance=2,
        recover_in_battle=lambda coord, tolerance: False,
    )
    assert ScriptTask.battle_relock_recover_position(task) is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
toolkit\python.exe -m pytest tests/tasks/YuwaiMimang -q
```

预期：`FAILED`，配置仍有 `fast_relock`；`preset_battle_loop` 仍调用 `acquire_target`；`battle_relock_recover_position` 不存在。

- [ ] **Step 3: 删除 `fast_relock` 配置**

在 `tasks/YuwaiMimang/config.py` 删除：

```python
    # 目标死后原地补锁（连续刷怪场景。单 boss / 刷怪间隔长时关掉，走正常流程）
    fast_relock: bool = Field(default=True, description='fast_relock_help')
```

并更新 `YuwaiBattleConfig` 的 docstring：删掉 `fast_relock` 那一段，保留 `target_names` 说明。

- [ ] **Step 4: 删除旧的锁定/补锁实现**

在 `tasks/YuwaiMimang/script_task.py`：

1. 删除 import：

```python
from tasks.Battle.target_action import ACTION_ATTACK
```

2. 删除常量与类属性：

```python
    TARGET_SWITCH_MAX = 8
    TARGET_SWITCH_TIMEOUT = 8
    TARGET_WARN_AFTER = 10
    _target_fail_count = 0
```

3. 删除 `acquire_target()`、`relock_target()`、`battle_target_ok()` 三个方法。
4. 顶部模块 docstring 第 14~16 行改为：

```text
- 只打「战斗目标」白名单（默认 小鬼,鬼王,鬼将）里的怪：每轮现查目标名，
  列表外的怪（例如路过的玩家）不碰（见 `battle_target_ok`）；
- 目标丢失点一次「目标」切换，下一轮截图继续判断；连续切不到时每 60s 回一次打怪点。
```

- [ ] **Step 5: 改造 `preset_battle_loop`**

把方法内从 `names = ...` 到循环结束的代码替换为：

```python
        names = self.battle_target_names() if names is None else names
        include_attack = self.battle_use_normal_attack()
        preset = self.battle_preset()
        logger.hr(f'开始按预设打怪（{preset["name"]}）')
        logger.attr('普通攻击', '开启' if include_attack else '关闭（技能全冷却时只能干等）')
        self.reset_battle_progress(end)
        self._battle_coord = coord
        self._battle_tolerance = tolerance
        self._target_fail_count = 0

        slots = []
        progress = {'casts': 0, 'last_repair': datetime.now()}

        def on_cast(_casts: int) -> None:
            """每点击一次技能：按累计次数做位置检查 / 中途修理"""
            progress['casts'] += 1
            if progress['casts'] % self.RECOVER_EVERY_CASTS:
                return
            self.recover_in_battle(coord, tolerance)
            if self.need_repair(cfg, progress['last_repair'], end):
                self.repair_in_battle(coord, tolerance)
                progress['last_repair'] = datetime.now()
            logger.attr('打怪进度', f'casts={progress["casts"]}')

        with suppress_control_input_log():
            while datetime.now() < end:
                self.reset_records()

                if not slots:
                    slots = self.recognize_skill_slots()
                    if not slots:
                        if self.battle_has_pending_slots():
                            logger.info('技能位都在冷却中未识别，等冷却结束后重认')
                        else:
                            logger.warning('技能位一直认不出来（技能栏被挡 / 素材不匹配），'
                                           '本时段不再盲点技能位，收工')
                            break

                # 未识别且在冷却的技能位会在循环里冷却结束后重认并补进计划
                slots = self.battle_current_slots() or slots
                reason = self.battle_skill_loop(
                    slots,
                    check_stop=self.battle_stop_reason,
                    target_check=lambda image: self.battle_target_ok(names, image),
                    include_attack=include_attack,
                    attack_interval=self.NORMAL_ATTACK_INTERVAL,
                    on_cast=on_cast,
                    on_target_lost=self.battle_relock_once)

                if reason == 'window_end':
                    break
                if reason == 'timeout':
                    logger.warning('技能循环超时，重新锁目标继续')
                self.device.sleep(0.2)

        logger.attr('打怪结束', f'共释放 {progress["casts"]} 次技能')
```

同时在 `preset_battle_loop` 之后加入：

```python
    def battle_relock_recover_position(self) -> bool:
        """连续切不到目标时回打怪点（失败也不阻塞继续刷怪）"""
        self.recover_in_battle(self._battle_coord, self._battle_tolerance)
        return True
```

并把方法 docstring 中「技能位只在第一次锁到目标后识别一次」改为「技能位只在第一次进入循环时识别一次」。

- [ ] **Step 6: 运行测试确认通过**

```bash
toolkit\python.exe -m pytest tests/tasks/YuwaiMimang -q
```

预期：全部 `passed`。

- [ ] **Step 7: 提交**

```bash
git add tasks/YuwaiMimang tests/tasks/YuwaiMimang
git commit -m "refactor(YuwaiMimang): 锁定流程并入战斗循环"
```

---

### Task 3: 迷窟仙境迁移

**Files:**
- Modify: `tasks/MikuXianjing/script_task.py:251-282`
- Modify: `tasks/MikuXianjing/config.py:18-22`
- Test: `tests/tasks/MikuXianjing/test_miku_xianjing_script.py`、`tests/tasks/MikuXianjing/test_miku_xianjing_config.py`

- [ ] **Step 1: 写失败测试**

在 `tests/tasks/MikuXianjing/test_miku_xianjing_config.py` 把：

```python
    assert cfg.battle_config.fast_relock is True
```

替换为：

```python
    assert not hasattr(cfg.battle_config, 'fast_relock')
```

在 `tests/tasks/MikuXianjing/test_miku_xianjing_script.py` 末尾追加：

```python
class _StopBattleLoop(Exception):
    pass


def test_progress_battle_loop_no_acquire_and_uses_relock():
    loops = []
    recognized = []

    def battle_skill_loop(*args, **kwargs):
        loops.append(kwargs)
        if len(loops) > 1:
            raise _StopBattleLoop
        return 'timeout'

    task = SimpleNamespace(
        parse_coord=lambda text: (308, 81),
        battle_target_names=lambda: ['修仙小妖'],
        battle_use_normal_attack=lambda: False,
        battle_preset=lambda: {'name': '默认预设'},
        recover_position=lambda coord, tolerance, timeout=None: True,
        ui_close_menu=lambda: None,
        screenshot=lambda: 'img',
        reset_records=lambda: None,
        battle_stop_reason=lambda image=None: None,
        recognize_skill_slots=lambda: recognized.append('slots') or [0, 1],
        battle_current_slots=lambda: [0, 1],
        battle_skill_loop=battle_skill_loop,
        battle_target_ok=lambda names, image=None: True,
        battle_relock_once=lambda: False,
        NORMAL_ATTACK_INTERVAL=1.0,
        RECOVER_EVERY_CASTS=6,
        recover_in_battle=lambda coord, tolerance: True,
        need_repair=lambda cfg, last_repair, end: False,
        device=SimpleNamespace(image='img', sleep=lambda seconds: None),
    )
    cfg = SimpleNamespace(battle_coord='308,81', battle_tolerance=2)

    with pytest.raises(_StopBattleLoop):
        ScriptTask.progress_battle_loop(task, cfg)

    assert len(recognized) == 1
    assert len(loops) == 2
    assert loops[0]['on_target_lost'] is task.battle_relock_once
    assert task._battle_coord == (308, 81)
    assert task._battle_tolerance == 2
```

- [ ] **Step 2: 运行测试确认失败**

```bash
toolkit\python.exe -m pytest tests/tasks/MikuXianjing -q
```

预期：`FAILED`，配置仍有 `fast_relock`；`progress_battle_loop` 仍调用 `acquire_target`。

- [ ] **Step 3: 删除 `fast_relock` 配置**

在 `tasks/MikuXianjing/config.py` 删除：

```python
    # 连续刷怪场景：目标死后原地快速补锁，减少重新走完整锁敌流程的耗时
    fast_relock: bool = Field(default=True, description='fast_relock_help')
```

- [ ] **Step 4: 改造 `progress_battle_loop`**

在 `tasks/MikuXianjing/script_task.py`：

1. 在 `self.ui_close_menu()` / `self.device.sleep(0.5)` 之后加：

```python
        self._battle_coord = coord
        self._battle_tolerance = tolerance
        self._target_fail_count = 0
```

2. 删除：

```python
                if not self.acquire_target(names):
                    self.device.sleep(1)
                    continue
```

3. 把 `on_target_lost=self.battle_on_target_lost(names)` 改为：

```python
                    on_target_lost=self.battle_relock_once)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
toolkit\python.exe -m pytest tests/tasks/MikuXianjing -q
```

预期：全部 `passed`。

- [ ] **Step 6: 提交**

```bash
git add tasks/MikuXianjing
git commit -m "refactor(MikuXianjing): 锁定流程并入战斗循环"
```

---

### Task 4: 回归验证

**Files:**
- 不修改文件

- [ ] **Step 1: 运行受影响套件**

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime tests/tasks/YuwaiMimang tests/tasks/MikuXianjing tests/tasks/GameUi/navigation tests/tasks/Battle -q
```

预期：全部 `passed`。

- [ ] **Step 2: 运行全量测试**

```bash
toolkit\python.exe -m pytest tests -q
```

预期：全部 `passed`。

- [ ] **Step 3: 确认改动范围**

```bash
git diff HEAD~3 --stat
```

预期：只包含：

```
tasks/GameUi/navigation/battle.py
tasks/LimitedTime/WildSnowman/script_task.py
tasks/YuwaiMimang/config.py
tasks/YuwaiMimang/script_task.py
tasks/MikuXianjing/config.py
tasks/MikuXianjing/script_task.py
tests/tasks/LimitedTime/test_wild_snowman_script.py
tests/tasks/YuwaiMimang/...
tests/tasks/MikuXianjing/...
```

`tasks/Battle/attack.py`、Boss/副本/战车等任务文件无改动。
