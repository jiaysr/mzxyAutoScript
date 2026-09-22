# This Python file uses the following encoding: utf-8
"""
地图功能真机调试任务（临时）：验证地点识别、小地图/世界地图/列表开关、传送与弹窗兼容

ACTION:
- check    只做页面开关校验（不传送，不消耗铜贝）
- teleport 传送到 TELEPORT_TARGET
- sweep    依次传送到所有地点，记录各地点的传送落点坐标（真机实测）
"""
import time

from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi

ACTION = 'check'
TELEPORT_TARGET = '牧野'
# sweep 只测这些地点（空列表 = 全部地点）；SWEEP_RETURN 是 sweep 结束后要回到的地点（空 = 回到开始时所在地点）
SWEEP_ONLY = []
SWEEP_RETURN = ''


class ScriptTask(GameUi):

    def run(self):
        if ACTION == 'check':
            self.map_check()
        elif ACTION == 'teleport':
            self.map_teleport_check()
        elif ACTION == 'sweep':
            self.map_sweep()
        raise TaskEnd('MapTest')

    # ------------------------------------------------------------------ 校验
    def log_state(self, tag: str) -> None:
        location = self.map_current_location()
        logger.info(f'STATE [{tag}] page={self.ui_current} location={location}')

    def map_check(self) -> None:
        logger.hr('Map check')
        self.ui_get_current_page()
        self.log_state('start')
        location = self.map_current_location()
        if location is not None:
            logger.info(f'INITIAL CHECK [{location[0]}] {location[1]},{location[2]} -> '
                        f'{self.map_in_location_initial(location[0])}')

        self.map_open_minimap()
        self.ui_get_current_page()
        self.log_state('minimap')

        self.map_close_minimap()
        self.ui_get_current_page()
        self.log_state('minimap closed')

        self.map_open_world_map_list()
        self.ui_get_current_page()
        self.log_state('world map list')
        names = [item.ocr_text for item in
                 self.O_MAP_WORLD_MAP_LIST.detect_and_ocr(self.device.image, logDisplay=False)]
        logger.info(f'LIST entries: {names}')
        for location in self.MAP_LOCATIONS:
            coord = self.map_find_list_location(location)
            logger.info(f'LIST find [{location}] -> {coord}')

        self.map_close_world_map_list()
        self.ui_get_current_page()
        self.log_state('list closed')

        self.map_close_world_map()
        self.ui_get_current_page()
        self.log_state('world map closed')

    def map_teleport_check(self) -> None:
        logger.hr(f'Map teleport check -> {TELEPORT_TARGET}')
        self.log_state('before')
        ok = self.map_teleport(TELEPORT_TARGET)
        logger.info(f'MAPTEST teleport [{TELEPORT_TARGET}] -> {ok}')
        self.log_state('after')

    def map_read_location_retry(self, times: int = 5):
        """传送落地后读取地点坐标：标签偶尔会有一两秒读不出来，多试几次"""
        for _ in range(times):
            self.device.stuck_record_clear()
            position = self.map_current_location()
            if position is not None:
                return position
            time.sleep(1)
        return None

    def map_sweep(self) -> None:
        logger.hr('Map sweep: measure every location initial position')
        current = self.map_read_location_retry()
        if current is None:
            logger.error('Cannot read current location')
            return
        if SWEEP_ONLY:
            order = [location for location in SWEEP_ONLY if location != current[0]]
        else:
            order = [location for location in self.MAP_LOCATIONS if location != current[0]]
            # 全部地点测完后传送回开始时所在地点（顺便实测该地点的落点）
            order.append(current[0])
        if SWEEP_RETURN and SWEEP_RETURN not in order:
            order.append(SWEEP_RETURN)
        logger.info(f'MAPTEST start at [{current[0]}] {current[1]},{current[2]}, order={order}')

        results = {}
        for location in order:
            if not self.map_teleport(location):
                logger.error(f'MAPTEST teleport [{location}] failed')
                continue
            position = self.map_read_location_retry()
            if position is None:
                logger.error(f'MAPTEST cannot read location after teleport to [{location}]')
                continue
            results[location] = (position[1], position[2])
            logger.info(f'MAPTEST RESULT [{location}] -> ({position[1]}, {position[2]}) '
                        f'(ocr name={position[0]})')

        logger.info(f'MAPTEST ALL RESULTS: {results}')
        lines = ['MAP_INITIAL_POS = {']
        for location in self.MAP_LOCATIONS:
            if location in results:
                lines.append(f'    # {location}')
                lines.append(f"    '{location}': {results[location]},")
        lines.append('}')
        for line in lines:
            logger.info(line)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
