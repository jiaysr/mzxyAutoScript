# This Python file uses the following encoding: utf-8
"""
战斗试打任务：识别技能栏 -> 按预设优先级循环攻击（暂时没有停止条件）

行为（实现在 `tasks/Battle/attack.py: battle_attack_loop()`）：
1. 截一帧识别 7 个技能位分别装的是什么技能；
2. 按预设算出优先级顺序（默认：任务配置 -> 系统预设，也可以在下面 PRESET 里写死预设 id）；
3. 一直循环：
   - 每次从优先级最高的技能往下挑「冷却好」的那个；
   - 点它 CLICKS_PER_SKILL 次（默认 3 次），然后轮询确认它真的进入冷却；
   - 回到队首重新挑 —— 所以高优先级的技能一冷却好就会立刻插队先放；
   - 技能全在冷却时只补普通攻击（按 ATTACK_INTERVAL 节流），不会硬点冷却中的技能。

停止逻辑还没定，先做成无限循环：Ctrl+C 或者直接关进程结束。
以后要停，给 `battle_attack_loop(stop_check=...)` 传个判断函数即可。

直接运行（项目根目录，游戏停在能看到技能栏的画面）：
    toolkit\\python.exe tasks\\Battle\\script_task.py

想固定用某个预设：把 PRESET 改成预设 id（比如 'preset_1'）。
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
from tasks.GameUi.game_ui import GameUi


class ScriptTask(GameUi):
    # 用哪个预设：'' = 任务配置（这里没有任务，等于系统预设）；也可以直接写预设 id
    PRESET = 'preset_1'
    # 每个技能连点几次（点完再判断是否进入冷却）
    CLICKS_PER_SKILL = 3
    # 连点间隔（秒）；None = BattleAttack.BATTLE_CLICK_INTERVAL（0.2）
    CLICK_INTERVAL = None
    # 点完等它进入冷却的最长等待（秒）
    COOLDOWN_TIMEOUT = 2.0
    # 冷却状态判不出来（没有基准素材）的技能，点过之后先晾多久（秒）
    UNKNOWN_WAIT = 1.0
    # 没有技能可放时，普通攻击的最小间隔（秒）
    ATTACK_INTERVAL = 1.0
    # 每放 N 次技能重新识别一次技能栏（换装备后用）；0 = 只识别一次
    REIDENTIFY_CASTS = 0

    def run(self) -> None:
        logger.hr('战斗试打：按预设优先级循环攻击', level=1)
        try:
            self.battle_attack_loop(
                preset=self.PRESET,
                clicks_per_skill=self.CLICKS_PER_SKILL,
                click_interval=self.CLICK_INTERVAL,
                cooldown_timeout=self.COOLDOWN_TIMEOUT,
                unknown_wait=self.UNKNOWN_WAIT,
                attack_interval=self.ATTACK_INTERVAL,
                reidentify_casts=self.REIDENTIFY_CASTS,
            )
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
