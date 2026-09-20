# This Python file uses the following encoding: utf-8
from pathlib import Path
from typing import TYPE_CHECKING

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.GlobalGame.assets import GlobalGameAssets

if TYPE_CHECKING:
    from module.config.config import Config
    from module.device.device import Device


class GlobalGame(GlobalGameAssets):
    """
    全局处理：所有任务的 screenshot 都会经过 handle_death / handle_popup
    - 角色阵亡时点击「返回村子」复活，保留任务进度并立即重跑当前任务
    - 出现已知弹窗时点击对应的关闭区域，避免弹窗挡住任务操作
    """

    if TYPE_CHECKING:
        # 以下成员由子类 BaseTask 提供，这里仅作类型声明（运行时不生效）
        config: Config
        device: Device

        def appear(self, target, interval: float = None, threshold: float = None): ...

        def click(self, click=None, interval: float = None) -> bool: ...

    # 任务重跑时需要保留的进度记录 {(config_name, command): record}
    _task_records: dict = {}

    # 全局弹窗处理：识别到特征点后点击对应的关闭区域
    # 素材放 tasks/GlobalGame/popup/，命名约定：
    #   特征点   itemName = popup_<名字>        -> 常量 I_POPUP_XXX
    #   关闭区域 itemName = popup_<名字>_close  -> 常量 C_POPUP_XXX_CLOSE
    # 两者自动按名称配对，无需改代码；也可用 popup_close 显式补充 [(特征, 关闭区域), ...]
    popup_close: list = []
    # 弹窗检查间隔（秒）：避免每帧截图都做大量模板匹配
    popup_check_interval: float = 1.0

    def handle_popup(self) -> None:
        """
        检测到已知弹窗时点击其关闭区域，调用前 device.image 需为最新截图
        """
        timer = getattr(self, '_popup_timer', None)
        if timer is None:
            timer = Timer(self.popup_check_interval).start()
            self._popup_timer = timer
        if not timer.reached():
            return
        timer.reset()

        for check, close in self._popup_pairs():
            if self.appear(check, interval=1):
                logger.info(f'Close popup {check.name}')
                self.click(close, interval=1)
                self.device.click_record_clear()
                return

    def _popup_pairs(self) -> list:
        """
        收集弹窗处理项：显式 popup_close + 按命名约定自动配对
        素材文件缺失时跳过并告警，避免录制不全导致任务中断
        """
        pairs = []
        for check, close in list(self.popup_close):
            if self._popup_ready(check):
                pairs.append((check, close))
        for name in dir(type(self)):
            if not name.startswith('I_POPUP_'):
                continue
            check = getattr(type(self), name)
            if not isinstance(check, RuleImage):
                continue
            close_name = f'C_POPUP_{name[len("I_POPUP_"):]}_CLOSE'
            close = getattr(type(self), close_name, None)
            if isinstance(close, RuleClick) and self._popup_ready(check):
                pairs.append((check, close))
        return pairs

    @staticmethod
    def _popup_ready(check) -> bool:
        """弹窗特征素材是否存在"""
        if Path(check.file).exists():
            return True
        logger.warning(f'Popup image missing: {check.file}')
        return False

    def handle_death(self) -> None:
        """
        检测到死亡弹窗时复活并重跑当前任务，调用前 device.image 需为最新截图
        """
        if not self.appear(self.I_DEATH_DIALOG):
            return

        logger.warning('Character died')
        self.revive()
        self.rerun_task()

    def revive(self, retry: int = 3, timeout: int = 30) -> bool:
        """
        点击「返回村子」直到死亡弹窗消失
        """
        logger.hr('Revive')
        timer = Timer(timeout).start()
        while 1:
            self.device.screenshot()
            if not self.appear(self.I_DEATH_DIALOG):
                logger.info('Revived at spawn point')
                return True
            if retry <= 0:
                raise GameStuckError('Unable to revive')
            if self.click(self.C_DEATH_RETURN, interval=1):
                retry -= 1
                logger.info('Click return to village')
            if timer.reached():
                raise GameStuckError('Revive timeout')

    # ---------------------------------------------------------------- 任务重跑
    def rerun_task(self) -> None:
        """
        保存当前任务的进度记录，并把当前任务设为立即重跑
        """
        command = self.current_task_command()
        if not command:
            raise TaskEnd('Character died')

        record = self.save_task_record()
        if record:
            self._task_records[(self.config.config_name, command)] = record
            logger.attr('Task record', record)
        logger.warning(f'Rerun task `{command}`')
        self.config.task_call(command)
        raise TaskEnd(f'Character died, `{command}` will rerun')

    def save_task_record(self) -> dict:
        """
        任务自定义：返回重跑时需要保留的进度记录，空 dict 表示不需要保留
        """
        return {}

    def load_task_record(self, record: dict) -> bool:
        """
        任务自定义：恢复进度记录，返回 True 表示记录已被使用
        """
        return False

    def restore_task_record(self) -> bool:
        """
        取出并恢复上一次重跑前保存的进度记录，恢复成功则删除记录
        """
        command = self.current_task_command()
        key = (self.config.config_name, command)
        record = self._task_records.get(key)
        if not record:
            return False
        if not self.load_task_record(record):
            return False
        self._task_records.pop(key, None)
        return True

    def clear_task_record(self) -> None:
        """
        任务正常结束时清掉残留的重跑记录
        """
        self._task_records.pop((self.config.config_name, self.current_task_command()), None)

    # ---------------------------------------------------------------- 任务名
    def current_task_command(self) -> str:
        """
        当前任务名：调度器运行时取 running_task，直跑时按模块路径取任务目录名
        """
        running = self.config.model.running_task
        if running:
            return running
        parts = type(self).__module__.split('.')
        if len(parts) >= 2 and parts[0] == 'tasks':
            return parts[1]
        return ''
