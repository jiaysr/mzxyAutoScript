# This Python file uses the following encoding: utf-8
from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.Challenge.assets import ChallengeAssets
from tasks.base_task import BaseTask


class ScriptTask(BaseTask, ChallengeAssets):
    """
    挑战页：
    1. 点击左上角头像进入个人信息（角色）面板
    2. 点击左侧「挑战」页签进入挑战页
    """

    def run(self) -> None:
        self.reset_records()

        self.enter_character_panel()
        self.enter_challenge_page()

        logger.info('Reached the challenge page')
        raise TaskEnd('Challenge')

    def reset_records(self) -> None:
        """
        点击前清空卡死与连点记录
        """
        self.device.stuck_record_clear()
        self.device.click_record_clear()

    def enter_character_panel(self, timeout: int = 10) -> bool:
        """
        点击左上角头像，等待个人信息（角色）面板出现
        已经在挑战页时直接返回 False，表示无需再打开面板
        """
        logger.hr('Enter character panel')
        timer = Timer(timeout).start()
        while 1:
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_CHARACTER_PANEL):
                logger.info('Character panel appear')
                return True
            if self.appear(self.I_CHALLENGE_PAGE):
                logger.info('Challenge page is already open')
                return False
            if timer.reached():
                raise GameStuckError('Character panel does not appear')
            self.click(self.C_AVATAR, interval=1)
            self.device.sleep(0.5)

    def enter_challenge_page(self, timeout: int = 10) -> bool:
        """
        点击左侧「挑战」页签，等待挑战页出现
        """
        logger.hr('Enter challenge page')
        timer = Timer(timeout).start()
        while 1:
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_CHALLENGE_PAGE):
                logger.info('Challenge page appear')
                return True
            if timer.reached():
                raise GameStuckError('Challenge page does not appear')
            self.click(self.C_CHALLENGE_TAB, interval=1)
            self.device.sleep(0.5)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    ScriptTask(config, device).run()
