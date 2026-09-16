# This Python file uses the following encoding: utf-8
from module.config.client import CLIENT_OFFICIAL, client
from module.logger import logger

if client == CLIENT_OFFICIAL:
    from tasks.Restart.login_official import RestartOfficial as _LoginHandler
else:
    from tasks.Restart.login_x7 import RestartX7 as _LoginHandler

logger.attr('Login handler', _LoginHandler.__name__)


class ScriptTask(_LoginHandler):
    """
    重启任务：启动游戏并登录，按游戏客户端版本分派登录流程
    """


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    task = ScriptTask(config, device)
    task.app_restart()
