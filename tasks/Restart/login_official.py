# This Python file uses the following encoding: utf-8
from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.Restart.restart_base import RestartBase


class RestartOfficial(RestartBase):
    """
    官方版登录流程（接口预留）

    官方版与小七版的登录页、SDK 弹窗不同，实现时复制本文件为独立流程：
    _app_handle_login 里按「开屏 -> 登录页 -> 公告 -> 主界面」拆分步骤，
    资产放到 tasks/Restart/official/ 下单独标注生成。
    """

    def _app_handle_login(self) -> bool:
        logger.critical('Official client login is not implemented yet')
        raise RequestHumanTakeover('Official client login is not implemented yet')
