# This Python file uses the following encoding: utf-8
"""
战斗试打任务：锁定目标 -> 识别技能栏 -> 按预设优先级「点一次 + 校验」循环

行为（与仙府九重天任务一致，实现在 BattleAttack.battle_skill_loop）：
1. 收起右上角菜单，锁定目标：先模板匹配确认已锁定，没锁到就点「目标」切换；
   右下角动作按钮必须是「攻击」（可攻击目标）。
2. 截一帧识别 7 个技能位分别装的是什么技能，按预设算优先级。
3. 循环（队列式，不按轮）：
   - 按优先级顺序释放技能；
   - 每个技能双击（同一点快速点两次）-> 判断目标 -> 判断是否进入冷却；
     没进冷切就排到「下一个技能」后面再试一次，同时只允许一个延后技能；
     下一个技能也没进冷切时不重排，继续释放之前延后的那个；
   - 所有技能都在冷却时才释放普通攻击；
   - 目标丢失 / 不可攻击时自动结束。
4. 目标丢失 / 不可攻击时自动结束（不会无限连点）。

目标：
- `TARGET_NAME = ''`：不校验目标名，适用于「地图里只有一个可锁定目标」的场景（先测这个）；
- 多个目标时把目标名写进去（支持逗号分隔），锁定后会 OCR 名字，对不上就继续切目标。

直接运行（项目根目录，游戏停在能看到技能栏的画面，且附近有可攻击目标）：
    toolkit\\python.exe tasks\\Battle\\script_task.py

想固定用某个预设：改 PRESET（预设 id，比如 'preset_1'）。
"""
import os
import sys
from pathlib import Path

# 让「直接运行本文件」也能 import 到项目里的模块（正常任务调度里走不到这里）
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if __name__ == '__main__':
    os.chdir(_PROJECT_ROOT)

from module.exception import TaskEnd
from module.logger import logger
from tasks.Battle.presets import resolve_preset
from tasks.GameUi.game_ui import GameUi


class ScriptTask(GameUi):
    # 用哪个预设：预设 id；'' = 系统预设
    PRESET = 'preset_1'
    # 目标名：'' = 不校验（单目标场景）；多个目标时填名字，支持逗号分隔
    TARGET_NAME = ''
    # 每个技能最多尝试几次（含第一次）：每次 = 一次双击，没进冷切就重试，超过跳过换下一个技能
    SKILL_MAX_ATTEMPTS = 3
    # 点完等它进入冷却的最长时间 / 轮询间隔（秒）
    COOLDOWN_TIMEOUT = 1.2
    COOLDOWN_POLL = 0.1
    # 全部技能冷却时是否点普通攻击（试打脚本没有任务配置，默认关闭）
    USE_NORMAL_ATTACK = False

    def run(self) -> None:
        target_name = self.TARGET_NAME.strip() or None
        logger.hr(f'战斗试打：目标={target_name or "不指定"} '
                  f'预设={self.PRESET or "系统预设"}', level=1)

        # 收起菜单并锁定目标（指定名称时，名字对不上会继续切目标）
        if not self.ui_ensure_target_locked(target_name=target_name):
            logger.warning('未能锁定可攻击目标，试打结束')
            raise TaskEnd('Battle')

        # 识别技能栏，算释放顺序
        self.screenshot()
        preset = None
        if self.PRESET:
            preset = resolve_preset(self.config.battle, self.PRESET, self.battle_catalog())
        plan = self.battle_plan(preset=preset)
        if plan:
            slots = [entry.slot for entry in plan]
            logger.info(f'技能释放顺序：{[slot + 1 for slot in slots]}')
        else:
            logger.warning('技能位识别失败，按 7 个技能位逐个尝试')
            slots = list(range(len(self.BATTLE_SKILL_REGIONS)))

        try:
            status = self.battle_skill_loop(
                slots,
                check_stop=lambda image: None if self.ui_target_attackable(image)
                else 'target_lost',
                target_check=lambda image: self.ui_target_attackable(image),
                timeout=None,
                attack_interval=1.0,
                include_attack=self.USE_NORMAL_ATTACK,
                max_attempts=self.SKILL_MAX_ATTEMPTS,
                cooldown_timeout=self.COOLDOWN_TIMEOUT,
                poll_interval=self.COOLDOWN_POLL,
            )
            logger.info(f'试打结束：{status}')
        finally:
            logger.info('战斗试打结束')
        raise TaskEnd('Battle')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass
