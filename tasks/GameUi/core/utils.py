# This Python file uses the following encoding: utf-8
"""
通用杂项能力（UiUtils）：

- 页面缓存管理：ui_reset_current_page / ui_restart_game / ui_button_interval_reset
- 长等待辅助：reset_records（清卡死与连点记录）
- 慢速滑动：ui_swipe_gentle
- 任务让路判断：higher_priority_task_due / higher_priority_task_names

由 GameUi 混入本类使用。
"""
from datetime import datetime

from module.config.config_manual import ConfigManual
from module.config.utils import convert_to_underscore
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class UiUtils(BaseTask, GameUiAssets):
    # 本任务在 ConfigManual.SCHEDULER_PRIORITY 中的名字（子类覆盖，用于让路判断）
    SCHEDULER_NAME: str = ''

    def ui_reset_current_page(self) -> None:
        """
        作废缓存的当前页面/模块
        界面在 ui_goto 之外发生变化后（重启游戏、角色寻路离开活动页、任务弹窗关闭回主页面等）调用，
        让下次导航重新识别页面，否则会按旧页面走错路径
        （如在主页面点角色面板的返回按钮而打开了地图）
        """
        self.ui_current = None
        self.ui_current_module = None

    def ui_restart_game(self) -> None:
        """
        复用重启任务的登录流程重启游戏（重启后界面回到主页面，页面缓存随之作废）
        """
        from tasks.Restart.script_task import ScriptTask as RestartTask
        RestartTask(self.config, self.device).app_restart()
        self.ui_reset_current_page()

    def ui_button_interval_reset(self, button):
        """
        Reset interval of some button to avoid mistaken clicks

        Args:
            button (Button):
        """
        if getattr(button, 'name', None) and button.name in self.interval_timer:
            self.interval_timer[button.name].reset()

    def reset_records(self) -> None:
        """
        长时间等待或连续滑动前，清空卡死与连点记录
        """
        self.device.stuck_record_clear()
        self.device.click_record_clear()

    def ui_swipe_gentle(self, p1: tuple, p2: tuple, steps: int = 3, step_delay: float = 0.2) -> None:
        """
        慢速滑动：把整段位移拆成多段小滑动，每段之间停顿
        （minitouch 的滑动速度固定且很快，整段一次滑容易甩过头、滑完立刻截图也认不准）
        :param p1: 起点
        :param p2: 终点
        :param steps: 拆分段数
        :param step_delay: 每段之间的停顿（秒）
        """
        points = [(int(p1[0] + (p2[0] - p1[0]) * i / steps),
                   int(p1[1] + (p2[1] - p1[1]) * i / steps)) for i in range(steps + 1)]
        for start, end in zip(points, points[1:]):
            self.device.swipe(p1=start, p2=end)
            self.device.click_record_clear()
            self.device.sleep(step_delay)

    def higher_priority_task_due(self) -> bool:
        """
        是否有调度优先级高于本任务、且已使能且已到期的任务（用于长时间等待时让路）
        优先级顺序取自 ConfigManual.SCHEDULER_PRIORITY，本任务名由 SCHEDULER_NAME 指定
        """
        now = datetime.now()
        for name in self.higher_priority_task_names():
            task = getattr(self.config.model, convert_to_underscore(name), None)
            scheduler = getattr(task, 'scheduler', None)
            if scheduler is None or not scheduler.enable:
                continue
            if scheduler.next_run <= now:
                logger.attr('Higher priority task', f'{name} {scheduler.next_run}')
                return True
        return False

    @classmethod
    def higher_priority_task_names(cls) -> list[str]:
        """调度优先级高于本任务的任务名列表"""
        names = [name.strip() for name in ConfigManual.SCHEDULER_PRIORITY.split('>') if name.strip()]
        if cls.SCHEDULER_NAME in names:
            return names[:names.index(cls.SCHEDULER_NAME)]
        return names
