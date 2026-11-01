# This Python file uses the following encoding: utf-8
"""
战斗模块（Battle）

- `skills.json`：技能目录，5 个职业 x 4 个技能 = 20 条（霸刀/疾羽/影刺/巫祝/灵剑），字段含义见文件头 `_note`
- `catalog.py`：目录加载与顺序归一化（纯逻辑，不依赖设备）
- `config.py`：配置模型（通用设置 + 用户预设列表；默认预设是内建的、不落盘）
- `presets.py`：预设的读/建/改/删，默认预设只读、用户预设可基于其他预设复制
- `recognizer.py`：开战前识别 7 个技能位分别带了哪个技能
- `planner.py`：按「识别结果 + 预设顺序 + 禁用」算出实际点击顺序
- `cooldown.py`：技能冷却识别（亮度x彩度评分 + `SkillStateStabilizer` 2 帧防抖，不依赖设备）
- `attack.py`：战斗执行（点技能/看冷却/按预设攻击，混入任务类用；冷却状态默认防抖 2 帧）
  - `battle_attack()` 打 N 轮；`battle_attack_loop()` 一直打（等冷却、可传 stop_check）
- `script_task.py`：可直跑的试打任务（`python tasks/Battle/script_task.py`）
- `slots.py`：7 个技能位的坐标 + 图标本体区域（和冷却识别共用）
- `tools/record_skill_icon.py`：从真机截图录一个技能图标
"""
