# This Python file uses the following encoding: utf-8
"""
伏魔山涧（FumoShanjian）

当前进度：
  第 1 步：通过万剑冢的伏魔大使 NPC 打开对话。
    准备主页面 -> 确保在万剑冢 -> 打开小地图 -> 视口拖到右上角
    -> 点击小地图上的伏魔大使标记 -> 关闭小地图 -> 轮询「伏魔大使」对话模板。
  第 2 步：OCR 对话选项区找到「进入伏魔涧」的文字位置并点击
    -> 如果对话框还开着就点右下角「确定」-> 等右上角地点从万剑冢切走。

后续：副本内玩法。
注意：对话框里明确写了「进入伏魔涧后没有退路」，副本内玩法/退出还没实现，
      本任务暂时不适合放进日常调度。
"""
from module.base.timer import Timer
from module.exception import (GameNotRunningError,
                              GamePageUnknownError,
                              GameStuckError,
                              TaskEnd,
                              TaskFailed)
from module.logger import logger
from tasks.FumoShanjian.assets import FumoShanjianAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.pages.page import page_main


class ScriptTask(GameUi, FumoShanjianAssets):
    name = '伏魔山涧'

    # NPC 所在地图
    LOCATION = '万剑冢'

    def run(self) -> None:
        logger.hr('伏魔山涧')
        try:
            self.prepare_game()
            if not self.open_npc_dialog():
                raise TaskFailed('无法打开伏魔大使对话')
            logger.info('伏魔大使对话已打开')
            if not self.enter_dungeon():
                raise TaskFailed('进入伏魔涧失败')
            logger.info('已进入伏魔涧（副本内玩法尚未实现，本次到此结束）')
        finally:
            # 正常/异常都重排，避免 next_run 停在过去霸占调度
            self.set_next_run(task='FumoShanjian', success=True, finish=True)
        raise TaskEnd('FumoShanjian')

    # ---------------------------------------------------------------- 准备
    def prepare_game(self) -> None:
        """确保游戏在线并停留在主页面"""
        logger.hr('准备游戏（在线 + 主页面）')
        if not self.device.app_is_running():
            logger.info('游戏未运行，重启游戏')
            self.ui_restart_game()
            logger.info('游戏已登录到主页面')
            return
        try:
            self.ui_reset_current_page()
            if not self.ui_goto(page_main, timeout=60):
                raise GameStuckError('Main page does not appear')
            logger.info('游戏在线且在主页面')
        except (GameNotRunningError, GamePageUnknownError, GameStuckError):
            logger.warning('无法切回主页面，重启游戏')
            self.ui_restart_game()

    def clear_blocking_popups(self, max_close: int = 3) -> None:
        """
        关掉挡住小地图入口的残留弹窗（如上次留下的 NPC 对话框、活动弹窗）

        模态弹窗会吞掉小地图入口的点击，不关掉会连点无效并触发连点保护。
        """
        logger.hr('Clear blocking popups')
        for _ in range(max_close):
            self.reset_records()
            if not self.try_close_unknown_page(skip_screenshot=False):
                break
            self.device.sleep(0.5)
        self.map_close_main_popup()

    # ---------------------------------------------------------------- NPC 对话
    def ensure_location(self) -> None:
        """确保角色在万剑冢（已经在就不传送，世界地图列表不含当前地点）"""
        location = self.map_move_read_pos()
        if location is not None and self.map_name_match(location[0], self.LOCATION):
            logger.attr('Location', f'Already at [{location[0]}] {location[1]},{location[2]}, skip teleport')
            return
        if not self.map_teleport(self.LOCATION):
            raise GameStuckError(f'Cannot teleport to [{self.LOCATION}]')

    def open_npc_dialog(self) -> bool:
        """
        打开伏魔大使对话：小地图视口拖到右上角 -> 点 NPC 标记 -> 关小地图 -> 轮询对话模板
        """
        self.clear_blocking_popups()
        self.ensure_location()

        logger.hr('Open Fumo NPC dialog')
        self.map_close_minimap()
        self.device.sleep(0.3)
        if not self.map_open_minimap():
            raise GameStuckError('Minimap does not appear')
        if not self.map_pan_to_corner('top_right'):
            # 视口没到角落时点击坐标可能偏，先记下来；对话没出现时上层会报失败
            logger.warning('Minimap does not reach top_right, continue with the configured click point')
        self.click(self.C_FUMO_NPC)
        self.device.click_record_clear()
        self.map_close_minimap()

        cfg = self.config.fumo_shanjian.fumo_shanjian_config
        return self.wait_npc_dialog(timeout=int(cfg.npc_wait_timeout))

    def wait_npc_dialog(self, timeout: int = 20) -> bool:
        """轮询「伏魔大使」对话模板，出现返回 True"""
        logger.hr('Wait Fumo NPC dialog')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            if self.appear(self.I_FUMO_DIALOG):
                logger.info('Fumo NPC dialog appeared')
                return True
            self.device.sleep(0.3)
        logger.warning('Fumo NPC dialog does not appear')
        return False

    # ---------------------------------------------------------------- 进入副本
    def read_dialog_options(self) -> list[tuple[str, int, int]]:
        """
        OCR 伏魔大使对话框的选项区

        返回 [(文字, 屏幕x, 屏幕y)]，坐标是文字框中心，可直接点击。
        detect_and_ocr 返回的 box 是相对 ROI 的，要加上 ROI 左上角才是屏幕坐标。
        """
        self.screenshot()
        roi = self.O_FUMO_DIALOG_OPTIONS.roi
        options = []
        for item in self.O_FUMO_DIALOG_OPTIONS.detect_and_ocr(self.device.image, logDisplay=False):
            text = item.ocr_text.strip()
            if not text:
                continue
            xs = [point[0] for point in item.box]
            ys = [point[1] for point in item.box]
            x = int((min(xs) + max(xs)) / 2) + roi[0]
            y = int((min(ys) + max(ys)) / 2) + roi[1]
            options.append((text, x, y))
        return options

    def find_dialog_option(self, option: str, retry: int = 3) -> tuple[int, int] | None:
        """
        在对话选项里找 option 的文字位置

        用 ocr_name_match 容忍 OCR 形近字（如「涧」被认成「润」）；
        找不到返回 None。
        """
        for attempt in range(1, retry + 1):
            self.reset_records()
            options = self.read_dialog_options()
            logger.info(f'Dialog options: {[text for text, _, _ in options]}')
            for text, x, y in options:
                if not self.ocr_name_match(text, option):
                    continue
                logger.info(f'Dialog option [{text}] found at ({x},{y})')
                return x, y
            logger.warning(f'Dialog option [{option}] not found (attempt {attempt}/{retry})')
            self.device.sleep(0.4)
        return None

    def enter_dungeon(self) -> bool:
        """
        找到并点击「进入伏魔涧」，随后等地点切换到副本

        不同流程可能点完选项就直接进图，也可能还要再点一次「确定」，
        所以「确定」放到 wait_enter_dungeon 的轮询里按需点击。
        """
        cfg = self.config.fumo_shanjian.fumo_shanjian_config
        logger.hr(f'Enter dungeon [{cfg.enter_option}]')
        coord = self.find_dialog_option(cfg.enter_option)
        if coord is None:
            logger.warning(f'Enter option [{cfg.enter_option}] does not appear')
            return False
        logger.info(f'Click enter option [{cfg.enter_option}] at {coord}')
        self.device.click(x=coord[0], y=coord[1], control_name='fumo_dialog_option')
        self.device.click_record_clear()
        self.device.sleep(0.5)
        return self.wait_enter_dungeon(int(cfg.enter_wait))

    def wait_enter_dungeon(self, timeout: int = 20) -> bool:
        """
        等进入副本：右上角地点从万剑冢切走即成功

        对话框还开着时补点右下角「确定」：有的版本点完选项要确认一次，
        有的版本点选项就直接进图（这时模板已经匹配不到，不会多点）。
        """
        logger.hr('Wait entering Fumo dungeon')
        timer = Timer(timeout).start()
        confirm_timer = Timer(1.5).start()
        outside = 0
        while not timer.reached():
            self.reset_records()
            self.screenshot()
            location = self.map_current_location(image=self.device.image)
            if location is not None and not self.map_name_match(location[0], self.LOCATION):
                # 连续两帧读到新地点才算进图，避免地点 OCR 抖动误判
                outside += 1
                if outside >= 2:
                    logger.info(f'Entered dungeon: {location[0]} {location[1]},{location[2]}')
                    return True
            else:
                outside = 0
            if self.appear(self.I_FUMO_DIALOG_CONFIRM) and confirm_timer.reached_and_reset():
                logger.info('Click dialog confirm')
                self.click(self.I_FUMO_DIALOG_CONFIRM)
                self.device.click_record_clear()
            self.device.sleep(0.4)
        logger.warning(f'Still at [{self.LOCATION}] after {timeout}s, enter dungeon failed')
        return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).read_dialog_options()
    except TaskEnd:
        pass
