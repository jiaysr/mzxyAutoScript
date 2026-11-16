# 野外雪人锁定流程统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除野外雪人的首次完整重锁，首次锁定和战斗中切换统一走「点一次目标 → 回截图」；补菜单守卫，删除 `fast_relock` 配置。

**Architecture:** `battle_loop` 不再调用 `acquire_target`，技能位识别提前；`battle_relock_once` 增加菜单守卫和 60s 节流的位置检查；`normal_attack_until_lost` 目标丢失时也走补锁。共享战斗层和其他任务不改。

**Tech Stack:** Python 3.10、pytest（`toolkit\python.exe -m pytest`）、现有 `GameUi` / `BattleAttack` / 设备控制栈。

**Spec:** `docs/superpowers/specs/2026-11-16-wild-snowman-relock-unify-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `tasks/LimitedTime/WildSnowman/config.py` | 风雪人配置模型 | 删除 `WildSnowmanBattleConfig`，`battle_config` 改用 `BattleTaskConfig` |
| `tasks/LimitedTime/WildSnowman/script_task.py` | 风雪人任务流程 | 菜单守卫、时间节流回位、删除首次锁定、普攻分支接补锁、常量清理 |
| `tests/tasks/LimitedTime/test_wild_snowman_config.py` | 配置测试 | 移除 `fast_relock` 断言 |
| `tests/tasks/LimitedTime/test_wild_snowman_script.py` | 流程测试 | 更新补锁/普攻/主循环用例，删除 `acquire_target` 用例 |

---

### Task 1: 删除 `fast_relock` 配置

**Files:**
- Modify: `tasks/LimitedTime/WildSnowman/config.py:29-31`、`:57`
- Test: `tests/tasks/LimitedTime/test_wild_snowman_config.py:21`

- [ ] **Step 1: 写失败测试**

把 `tests/tasks/LimitedTime/test_wild_snowman_config.py` 中：

```python
    assert cfg.battle_config.fast_relock is True
```

替换为：

```python
    assert cfg.battle_config.battle_preset == 'default'
    assert cfg.battle_config.use_normal_attack is False
    assert not hasattr(cfg.battle_config, 'fast_relock')
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_config.py -q
```

预期：`FAILED`，`assert not hasattr(cfg.battle_config, 'fast_relock')` 失败（字段仍存在）。

- [ ] **Step 3: 删除配置类**

在 `tasks/LimitedTime/WildSnowman/config.py` 删除：

```python
class WildSnowmanBattleConfig(BattleTaskConfig):
    # 连续刷怪场景：目标死后原地快速补锁，减少重新走完整锁敌流程的耗时
    fast_relock: bool = Field(default=True, description='fast_relock_help')
```

把 `WildSnowman` 里的：

```python
    battle_config: WildSnowmanBattleConfig = Field(default_factory=WildSnowmanBattleConfig)
```

替换为：

```python
    battle_config: BattleTaskConfig = Field(default_factory=BattleTaskConfig)
```

- [ ] **Step 4: 运行测试确认通过**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_config.py -q
```

预期：全部 `passed`。

- [ ] **Step 5: 提交**

```bash
git add tasks/LimitedTime/WildSnowman/config.py tests/tasks/LimitedTime/test_wild_snowman_config.py
git commit -m "refactor(WildSnowman): 删除 fast_relock 配置"
```

---

### Task 2: 菜单守卫 + 位置检查时间节流

**Files:**
- Modify: `tasks/LimitedTime/WildSnowman/script_task.py:17-19`（import）、`:49-60`（常量）、`:71`（run 初始化）、`:358-381`（`battle_relock_once`）
- Test: `tests/tasks/LimitedTime/test_wild_snowman_script.py:187-260`

- [ ] **Step 1: 替换失败测试**

删除 `tests/tasks/LimitedTime/test_wild_snowman_script.py` 里从 `def test_battle_relock_once_clicks_once_without_waiting():` 到 `test_battle_relock_once_stops_when_reposition_fails()` 结束的整段旧用例（共 5 个），替换为：

```python
def test_battle_relock_once_clicks_once_without_waiting():
    clicks = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: clicks.append('click') or True,
        return_to_battle_position=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert clicks == ['click']
    assert task._target_fail_count == 1


def test_battle_relock_once_aborts_on_reposition():
    clicks = []
    task = SimpleNamespace(
        _resume_reposition=True,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: clicks.append('click') or True,
        return_to_battle_position=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is False
    assert clicks == []


def test_battle_relock_once_closes_menu_before_switching():
    menu = []
    clicks = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: True,
        ui_close_menu=lambda abort_check=None: menu.append('close') or True,
        battle_switch_target_once=lambda: clicks.append('click') or True,
        return_to_battle_position=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert menu == ['close']
    assert clicks == []


def test_battle_relock_once_checks_position_after_interval():
    repositions = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        _next_relock_position_check=0.0,
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: True,
        return_to_battle_position=lambda: repositions.append('back') or True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert repositions == ['back']
    assert task._next_relock_position_check > 0


def test_battle_relock_once_skips_position_check_within_interval():
    repositions = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        _next_relock_position_check=float('inf'),
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: True,
        return_to_battle_position=lambda: repositions.append('back') or True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert repositions == []


def test_battle_relock_once_stops_when_reposition_fails():
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        _next_relock_position_check=0.0,
        RELOCK_POSITION_CHECK_SECONDS=60,
        menu_icons_visible=lambda: False,
        battle_switch_target_once=lambda: True,
        return_to_battle_position=lambda: False,
    )
    assert ScriptTask.battle_relock_once(task) is False
    assert task._resume_reposition is True
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q -k battle_relock_once
```

预期：`FAILED`。菜单用例报 `AttributeError: ... has no attribute 'menu_icons_visible'` 或未调用 `ui_close_menu`；位置节流用例未按 60s 窗口执行。

- [ ] **Step 3: 实现菜单守卫与时间节流**

在 `tasks/LimitedTime/WildSnowman/script_task.py` 顶部 `import math` 之后加：

```python
import time
```

在类常量区（`TARGET_WARN_AFTER = 10` 之后）加：

```python
    # 无目标时点目标的频率很高，位置检查按时间节流，避免每几秒读一次地图坐标
    RELOCK_POSITION_CHECK_SECONDS = 60
```

在 `run()` 的 `self._target_fail_count = 0` 之后加：

```python
        self._next_relock_position_check = 0.0
```

把 `battle_relock_once()` 整个方法替换为：

```python
    def battle_relock_once(self) -> bool:
        """
        目标丢失：点一次「目标」按钮切换，然后立刻回战斗循环的截图开头

        补锁内部不等待、不截图、不校验，把「是不是可攻击 + 名字对不对」
        留给下一轮 `battle_target_ok`。菜单展开时目标按钮会被盖住，先收起菜单，
        本轮不点目标。

        无目标时会连续点很多次，位置检查按 `RELOCK_POSITION_CHECK_SECONDS`
        节流：每 60s 读一次地图坐标，回不去就置 `_resume_reposition`。
        """
        if self._resume_reposition:
            return False
        if self.menu_icons_visible():
            self.ui_close_menu(abort_check=lambda: self._resume_reposition)
            return True
        self.battle_switch_target_once()
        self._target_fail_count += 1
        now = time.time()
        if now >= getattr(self, '_next_relock_position_check', 0.0):
            self._next_relock_position_check = now + self.RELOCK_POSITION_CHECK_SECONDS
            logger.warning(f'连续 {self._target_fail_count} 次没有切到白名单目标，'
                           f'检查打怪点位置')
            if not self.return_to_battle_position():
                self._resume_reposition = True
                return False
        return True
```

- [ ] **Step 4: 运行测试确认通过**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q -k "battle_relock_once or battle_target_ok"
```

预期：全部 `passed`。

- [ ] **Step 5: 提交**

```bash
git add tasks/LimitedTime/WildSnowman/script_task.py tests/tasks/LimitedTime/test_wild_snowman_script.py
git commit -m "feat(WildSnowman): 补锁前检查菜单并按时间节流回位"
```

---

### Task 3: 删除首次完整重锁，普攻分支接补锁

**Files:**
- Modify: `tasks/LimitedTime/WildSnowman/script_task.py:24`（import）、`:49-60`（常量）、`:218-282`（`battle_loop`）、`:319-339`（删除 `acquire_target`）、`:395-406`（`normal_attack_until_lost`）
- Test: `tests/tasks/LimitedTime/test_wild_snowman_script.py:130-143`、`:299-325`、`:349-388`

- [ ] **Step 1: 更新失败测试**

(a) 删除 `test_acquire_target_counts_fail_when_not_repositioning()` 整个函数。

(b) 把 `test_normal_attack_stops_on_player_without_hitting()` 的 `task = SimpleNamespace(...)` 改成：

```python
    task = SimpleNamespace(
        battle_stop_reason=lambda: None,
        screenshot=lambda: 'img',
        battle_target_ok=lambda names, image: False,
        battle_relock_once=lambda: False,
        battle_click_attack=lambda: attacks.append('hit'),
        device=SimpleNamespace(sleep=lambda seconds: None),
        NORMAL_ATTACK_INTERVAL=1.0,
    )
```

(c) 把 `test_normal_attack_hits_valid_then_stops_on_player()` 的 `task = SimpleNamespace(...)` 改成：

```python
    task = SimpleNamespace(
        battle_stop_reason=lambda: None,
        screenshot=lambda: 'img',
        battle_target_ok=lambda names, image: next(results),
        battle_relock_once=lambda: False,
        battle_click_attack=lambda: attacks.append('hit'),
        device=SimpleNamespace(sleep=lambda seconds: None),
        NORMAL_ATTACK_INTERVAL=1.0,
    )
```

(d) 在 `test_normal_attack_hits_valid_then_stops_on_player()` 之后追加：

```python
def test_normal_attack_relocks_then_attacks():
    attacks = []
    relocks = []
    results = iter([False, True])
    reasons = iter([None, None, 'recover'])
    task = SimpleNamespace(
        battle_stop_reason=lambda: next(reasons),
        screenshot=lambda: 'img',
        battle_target_ok=lambda names, image: next(results),
        battle_relock_once=lambda: relocks.append('relock') or True,
        battle_click_attack=lambda: attacks.append('hit'),
        device=SimpleNamespace(sleep=lambda seconds: None),
        NORMAL_ATTACK_INTERVAL=1.0,
    )
    assert ScriptTask.normal_attack_until_lost(task, ['可愛雪人']) == 'recover'
    assert relocks == ['relock']
    assert attacks == ['hit']
```

(e) 把 `test_battle_loop_recognizes_skill_slots_only_once()` 整个函数替换为：

```python
def test_battle_loop_recognizes_skill_slots_only_once():
    recognized = []
    loops = []

    def battle_skill_loop(*args, **kwargs):
        loops.append(kwargs)
        if len(loops) > 2:
            raise _StopBattleLoop
        return 'target_lost'

    task = SimpleNamespace(
        target_names=lambda: ['可爱雪人'],
        stop_for_others=lambda: False,
        stop_because_disabled=lambda: False,
        _resume_reposition=False,
        need_recover=lambda: False,
        recognize_skill_slots=lambda: recognized.append('slots') or [0, 1],
        battle_current_slots=lambda: [0, 1],
        battle_skill_loop=battle_skill_loop,
        battle_stop_reason=lambda image=None: None,
        battle_target_ok=lambda names, image=None: True,
        battle_use_normal_attack=lambda: False,
        battle_relock_once=lambda: False,
        on_cast=lambda casts: None,
        normal_attack_until_lost=lambda names: 'target_lost',
        NORMAL_ATTACK_INTERVAL=1.0,
        device=SimpleNamespace(sleep=lambda seconds: None),
    )

    with pytest.raises(_StopBattleLoop):
        ScriptTask.battle_loop(task)

    assert len(recognized) == 1
    assert len(loops) == 3
    assert loops[0]['on_target_lost'] is task.battle_relock_once
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q -k "battle_loop or normal_attack"
```

预期：`FAILED`。`battle_loop` 仍调用 `self.acquire_target`，假任务没有该属性；普攻用例仍直接返回 `'target_lost'`，未调用补锁。

- [ ] **Step 3: 删除首次锁定与相关常量**

在 `tasks/LimitedTime/WildSnowman/script_task.py`：

1. 删除 import：

```python
from tasks.Battle.target_action import ACTION_ATTACK
```

2. 删除常量：

```python
    # 目标切换次数/超时
    TARGET_SWITCH_MAX = 8
    TARGET_SWITCH_TIMEOUT = 10
    TARGET_WARN_AFTER = 10
```

3. 删除整个 `acquire_target()` 方法（从 `def acquire_target(self, names: list) -> bool:` 到 `return False`，位于 `# ---- 锁目标 / 技能` 注释之后、`battle_target_ok` 之前）。

- [ ] **Step 4: 修改 `battle_loop`**

把 `battle_loop()` 里的：

```python
            if not self.acquire_target(names):
                self.device.sleep(1)
                if self._target_fail_count % self.TARGET_WARN_AFTER == 0:
                    if not self.return_to_battle_position():
                        self._resume_reposition = True
                continue

            if not slots:
                slots = self.recognize_skill_slots()
```

替换为：

```python
            if not slots:
                slots = self.recognize_skill_slots()
```

并把方法开头的注释：

```python
        # 技能位只在本场任务第一次锁到目标时识别一次，之后目标丢失/换怪都复用。
```

替换为：

```python
        # 技能位只在本场任务第一次进入循环时识别一次，之后目标丢失/换怪都复用。
```

- [ ] **Step 5: 修改 `normal_attack_until_lost`**

把：

```python
            image = self.screenshot()
            if not self.battle_target_ok(names, image):
                return 'target_lost'
            self.battle_click_attack()
```

替换为：

```python
            image = self.screenshot()
            if not self.battle_target_ok(names, image):
                if not self.battle_relock_once():
                    return 'target_lost'
                continue
            self.battle_click_attack()
```

- [ ] **Step 6: 运行测试确认通过**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q
```

预期：全部 `passed`。

- [ ] **Step 7: 提交**

```bash
git add tasks/LimitedTime/WildSnowman/script_task.py tests/tasks/LimitedTime/test_wild_snowman_script.py
git commit -m "refactor(WildSnowman): 首次锁定并入战斗循环"
```

---

### Task 4: 回归验证

**Files:**
- 不修改文件

- [ ] **Step 1: 运行受影响套件**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime tests/tasks/GameUi/navigation tests/tasks/Battle -q
```

预期：全部 `passed`。

- [ ] **Step 2: 运行全量测试**

运行：

```bash
toolkit\python.exe -m pytest tests -q
```

预期：`365 passed` 或更多（新增用例后总数增加）。

- [ ] **Step 3: 确认改动范围**

运行：

```bash
git diff HEAD~3 --stat
```

预期：只包含以下文件：

```
tasks/LimitedTime/WildSnowman/config.py
tasks/LimitedTime/WildSnowman/script_task.py
tests/tasks/LimitedTime/test_wild_snowman_config.py
tests/tasks/LimitedTime/test_wild_snowman_script.py
```

共享层的 `tasks/Battle/attack.py`、`tasks/GameUi/navigation/battle.py` 和其他任务文件均无改动。
