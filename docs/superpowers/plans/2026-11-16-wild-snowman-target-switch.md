# 野外雪人目标丢失切换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 野外雪人战斗循环中目标丢失时，只点一次「目标」按钮切换，然后立刻回到截图开头，由下一轮统一校验。

**Architecture:** 点击机制放在共享的 `SimpleBattle`（新增 `battle_switch_target_once`，不等待/不截图/不校验）；切换策略放在 `WildSnowman`（新增 `battle_relock_once`，处理死亡中止、`fast_relock` 开关、连续失败回位），通过现有 `on_target_lost` 回调接入 `battle_skill_loop`。共享战斗循环和其他任务不改。

**Tech Stack:** Python 3.10、pytest（`toolkit\python.exe -m pytest`）、现有 `GameUi` / `BattleAttack` / 设备控制栈。

**Spec:** `docs/superpowers/specs/2026-11-16-wild-snowman-target-switch-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `tasks/GameUi/navigation/battle.py` | 战斗 UI 通用能力（目标锁定/补锁） | 新增 `battle_switch_target_once()`：点一次目标按钮 |
| `tasks/LimitedTime/WildSnowman/script_task.py` | 野外雪人任务流程 | 新增 `battle_relock_once()`；`battle_target_ok()` 成功时清零失败计数；`battle_loop()` 改用新回调 |
| `tests/tasks/GameUi/navigation/test_battle_logging.py` | 公共战斗层测试 | 新增点击机制的测试 |
| `tests/tasks/LimitedTime/test_wild_snowman_script.py` | 风雪人流程测试 | 新增切换策略测试；更新 `battle_loop` 假任务 |

---

### Task 1: 通用点击机制 `battle_switch_target_once()`

**Files:**
- Modify: `tasks/GameUi/navigation/battle.py:178`（`battle_relock_target` 之后、`battle_on_target_lost` 之前）
- Test: `tests/tasks/GameUi/navigation/test_battle_logging.py`

- [ ] **Step 1: 写失败测试**

在 `tests/tasks/GameUi/navigation/test_battle_logging.py` 末尾追加：

```python
def test_switch_target_once_clicks_without_waiting():
    class FakeDevice:
        def __init__(self):
            self.clicks = []
            self.cleared = 0

        def click(self, **kwargs):
            self.clicks.append(kwargs)

        def click_record_clear(self):
            self.cleared += 1

        def sleep(self, seconds):
            raise AssertionError('battle_switch_target_once 不应该等待')

    device = FakeDevice()
    task = SimpleNamespace(
        C_TARGET_BUTTON_CLICK=SimpleNamespace(coord=lambda: (11, 22)),
        device=device,
    )

    assert SimpleBattle.battle_switch_target_once(task) is True
    assert device.clicks == [{
        'x': 11, 'y': 22, 'control_name': 'BATTLE_TARGET', 'log': False,
    }]
    assert device.cleared == 1
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/GameUi/navigation/test_battle_logging.py::test_switch_target_once_clicks_without_waiting -q
```

预期：`FAILED`，报 `AttributeError: type object 'SimpleBattle' has no attribute 'battle_switch_target_once'`。

- [ ] **Step 3: 实现最小代码**

在 `tasks/GameUi/navigation/battle.py` 的 `battle_relock_target()` 方法结束后（当前第 178 行 `return False` 之后、`battle_on_target_lost` 之前）插入：

```python
    def battle_switch_target_once(self) -> bool:
        """
        点一次「目标」按钮切换目标（不等待、不截图、不校验）

        给「丢失后只切一次、下一轮截图再判」的节奏式流程用（野外雪人）：
        补锁的校验交给下一轮 `target_check`，省掉补锁内部的一次截图和一轮识别。
        """
        x, y = self.C_TARGET_BUTTON_CLICK.coord()
        self.device.click(x=x, y=y, control_name='BATTLE_TARGET', log=False)
        self.device.click_record_clear()
        return True
```

- [ ] **Step 4: 运行测试确认通过**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/GameUi/navigation/test_battle_logging.py -q
```

预期：全部 `passed`（包含原有 3 个测试）。

- [ ] **Step 5: 提交**

```bash
git add tasks/GameUi/navigation/battle.py tests/tasks/GameUi/navigation/test_battle_logging.py
git commit -m "feat(battle): 增加只点一次的目标切换方法"
```

---

### Task 2: 风雪人切换策略与循环接入

**Files:**
- Modify: `tasks/LimitedTime/WildSnowman/script_task.py:342-358`（`battle_target_ok`）
- Modify: `tasks/LimitedTime/WildSnowman/script_task.py:358` 之后（新增 `battle_relock_once`）
- Modify: `tasks/LimitedTime/WildSnowman/script_task.py:264-265`（`battle_loop` 的 `on_target_lost`）
- Test: `tests/tasks/LimitedTime/test_wild_snowman_script.py`

- [ ] **Step 1: 写失败测试**

在 `tests/tasks/LimitedTime/test_wild_snowman_script.py` 的 `test_battle_relock_target_aborts_before_clicking` 之后追加：

```python
def test_battle_relock_once_clicks_once_without_waiting():
    clicks = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        TARGET_WARN_AFTER=10,
        battle_fast_relock=lambda: True,
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
        TARGET_WARN_AFTER=10,
        battle_fast_relock=lambda: True,
        battle_switch_target_once=lambda: clicks.append('click') or True,
        return_to_battle_position=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is False
    assert clicks == []


def test_battle_relock_once_respects_fast_relock_off():
    clicks = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=0,
        TARGET_WARN_AFTER=10,
        battle_fast_relock=lambda: False,
        battle_switch_target_once=lambda: clicks.append('click') or True,
        return_to_battle_position=lambda: True,
    )
    assert ScriptTask.battle_relock_once(task) is False
    assert clicks == []


def test_battle_relock_once_repositions_after_warn_count():
    repositions = []
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=9,
        TARGET_WARN_AFTER=10,
        battle_fast_relock=lambda: True,
        battle_switch_target_once=lambda: True,
        return_to_battle_position=lambda: repositions.append('back') or True,
    )
    assert ScriptTask.battle_relock_once(task) is True
    assert repositions == ['back']
    assert task._resume_reposition is False


def test_battle_relock_once_stops_when_reposition_fails():
    task = SimpleNamespace(
        _resume_reposition=False,
        _target_fail_count=9,
        TARGET_WARN_AFTER=10,
        battle_fast_relock=lambda: True,
        battle_switch_target_once=lambda: True,
        return_to_battle_position=lambda: False,
    )
    assert ScriptTask.battle_relock_once(task) is False
    assert task._resume_reposition is True


def test_battle_target_ok_resets_fail_count():
    task = SimpleNamespace(
        _target_fail_count=7,
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '可愛雪人',
        ocr_name_match=ScriptTask.ocr_name_match,
    )
    assert ScriptTask.battle_target_ok(task, ['可愛雪人']) is True
    assert task._target_fail_count == 0
```

然后把 `test_battle_loop_recognizes_skill_slots_only_once` 假任务里的：

```python
        battle_on_target_lost=lambda names, abort_check=None: (lambda: False),
```

替换为：

```python
        battle_relock_once=lambda: False,
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q
```

预期：`FAILED`，新增用例报 `AttributeError: type object 'ScriptTask' has no attribute 'battle_relock_once'`；`test_battle_target_ok_resets_fail_count` 报 `AttributeError: '_target_fail_count'`。

- [ ] **Step 3: 实现 `battle_relock_once()`**

在 `tasks/LimitedTime/WildSnowman/script_task.py` 的 `battle_target_ok()` 之后、`recognize_skill_slots()` 之前插入：

```python
    def battle_relock_once(self) -> bool:
        """
        目标丢失：点一次「目标」按钮切换，然后立刻回战斗循环的截图开头

        和 `battle_on_target_lost` 的区别：补锁内部不等待、不截图、不校验，
        把「是不是可攻击 + 名字对不对」留给下一轮 `battle_target_ok`。
        雪人场景一个点持续刷怪，少一次截图/一轮识别，切换更快。

        连续 `TARGET_WARN_AFTER` 次都切不到白名单目标时，尝试回打怪点；
        回不去就置 `_resume_reposition`，让下一轮停止条件走 'reposition'。
        """
        if self._resume_reposition:
            return False
        if not self.battle_fast_relock():
            return False
        self.battle_switch_target_once()
        self._target_fail_count += 1
        if self._target_fail_count % self.TARGET_WARN_AFTER == 0:
            logger.warning(f'连续 {self._target_fail_count} 次没有切到白名单目标，'
                           f'尝试回到打怪点')
            if not self.return_to_battle_position():
                self._resume_reposition = True
                return False
        return True
```

- [ ] **Step 4: 修改 `battle_target_ok()` 清零失败计数**

把 `tasks/LimitedTime/WildSnowman/script_task.py` 中 `battle_target_ok()` 的结尾：

```python
        if not self.ui_target_attackable(image=image):
            return False
        name = self.ui_target_name()
        if not name:
            return True
        if any(self.ocr_name_match(name, expected) for expected in names):
            return True
        logger.warning(f'锁着的目标变成 [{name}]，不在白名单 {names}，停止放技能')
        return False
```

替换为：

```python
        if not self.ui_target_attackable(image=image):
            return False
        name = self.ui_target_name()
        if name and not any(self.ocr_name_match(name, expected) for expected in names):
            logger.warning(f'锁着的目标变成 [{name}]，不在白名单 {names}，停止放技能')
            return False
        self._target_fail_count = 0
        return True
```

行为保持不变：名字读不出来（空串）仍按「是目标」处理，只是成功时把连续失败计数清零。

- [ ] **Step 5: 循环接入新回调**

把 `battle_loop()` 里的：

```python
                    on_target_lost=self.battle_on_target_lost(
                        names, abort_check=lambda: self._resume_reposition),
```

替换为：

```python
                    on_target_lost=self.battle_relock_once,
```

`target_check` 和 `acquire_target(names)` 不变。

- [ ] **Step 6: 运行测试确认通过**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py -q
```

预期：全部 `passed`（33 个原有 + 6 个新增）。

- [ ] **Step 7: 提交**

```bash
git add tasks/LimitedTime/WildSnowman/script_task.py tests/tasks/LimitedTime/test_wild_snowman_script.py
git commit -m "feat(WildSnowman): 目标丢失只切一次目标后回到截图"
```

---

### Task 3: 回归验证

**Files:**
- 不修改文件

- [ ] **Step 1: 运行受影响套件**

运行：

```bash
toolkit\python.exe -m pytest tests/tasks/LimitedTime/test_wild_snowman_script.py tests/tasks/LimitedTime/test_wild_snowman_config.py tests/tasks/GameUi/navigation/test_battle_logging.py tests/tasks/Battle/test_battle_skill_loop.py -q
```

预期：全部 `passed`。

- [ ] **Step 2: 确认共享循环未受影响**

运行：

```bash
git diff HEAD~2 --stat
```

预期：只有以下文件被修改：

```
tasks/GameUi/navigation/battle.py
tasks/LimitedTime/WildSnowman/script_task.py
tests/tasks/GameUi/navigation/test_battle_logging.py
tests/tasks/LimitedTime/test_wild_snowman_script.py
```

`tasks/Battle/attack.py`、`tasks/GameUi/navigation/battle.py` 的现有方法、其他任务文件均无改动。
