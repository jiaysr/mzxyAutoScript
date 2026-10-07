# This Python file uses the following encoding: utf-8
"""
地图功能真机调试任务（临时）：验证地点识别、小地图/世界地图/列表开关、传送与弹窗兼容

ACTION:
- check    只做页面开关校验（不传送，不消耗铜贝）
- teleport 传送到 TELEPORT_TARGET
- sweep    依次传送到所有地点，记录各地点的传送落点坐标（真机实测）
- calibrate 校准移动轮盘的方向/速度（会让角色移动几秒）
- move     走到 MOVE_TARGET（为 None 时走到「当前位置 + MOVE_OFFSET」的就近点）
- path     依次走到 MOVE_PATH 里的每个坐标点
- bounds   朝四个方向各长按 BOUNDS_HOLD 秒，测量当前地图的范围（用于决定绘图网格）
"""
import time

from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi

ACTION = 'path'
TELEPORT_TARGET = '牧野'
# sweep 只测这些地点（空列表 = 全部地点）；SWEEP_RETURN 是 sweep 结束后要回到的地点（空 = 回到开始时所在地点）
SWEEP_ONLY = []
SWEEP_RETURN = ''
# move: 目标地图坐标（x, y）；为 None 时走到「当前位置 + MOVE_OFFSET」的就近点
MOVE_TARGET = None
MOVE_OFFSET = (-30, 0)
# move: 到达容差
# 单步可控位移下限约 2 个坐标单位（见 move.py MOVE_FINE_MIN_STEP_UNITS 实测），
# 容差需大于「单步位移 x 1.5」才有收敛希望，即 >= 3；
# 小于它会看到警告并快速失败（继续重试也只是在目标两侧来回跨）
MOVE_TOLERANCE = 3
# move: 超时（秒）。精调最后一步可能落在目标相邻格，需要多试几次才压得中
MOVE_TIMEOUT = 30
# path: 依次走到的一串坐标点（ACTION = 'path' 时用）
MOVE_PATH = [(340, 120), (280, 60), (303, 79)]
# bounds: 每个方向长按的时间（秒）
BOUNDS_HOLD = 8


class ScriptTask(GameUi):
    def run(self):
        if ACTION == 'check':
            self.map_check()
        elif ACTION == 'teleport':
            self.map_teleport_check()
        elif ACTION == 'sweep':
            self.map_sweep()
        elif ACTION == 'calibrate':
            self.map_move_calibrate_check()
        elif ACTION == 'move':
            self.map_move_check()
        elif ACTION == 'path':
            self.map_move_path_check()
        elif ACTION == 'bounds':
            self.map_bounds_check()
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

    def map_move_calibrate_check(self) -> None:
        logger.hr('Map move calibrate')
        if not self.map_move_ensure_main_page():
            logger.error('Not on main page, cannot calibrate')
            return
        self.log_state('before')
        result = self.map_move_calibrate()
        logger.info(f'MAPTEST calibration -> {result}')
        self.log_state('after')

    def map_move_check(self) -> None:
        logger.hr('Map move check')
        self.log_state('before')
        current = self.map_move_read_pos()
        if current is None:
            logger.error('Cannot read current position')
            return
        if MOVE_TARGET:
            target = MOVE_TARGET
        else:
            target = (current[1] + MOVE_OFFSET[0], current[2] + MOVE_OFFSET[1])
        logger.info(f'MAPTEST move [{current[0]}] {current[1]},{current[2]} -> {target} '
                    f'(tolerance {MOVE_TOLERANCE}, timeout {MOVE_TIMEOUT}s)')
        ok = self.map_move_to(target[0], target[1], map_name=current[0],
                              tolerance=MOVE_TOLERANCE, timeout=MOVE_TIMEOUT)
        logger.info(f'MAPTEST move [{target}] -> {ok}')
        self.log_state('after')

    def map_move_path_check(self) -> None:
        logger.hr('Map move path')
        self.log_state('before')
        done = self.map_move_path(MOVE_PATH, tolerance=MOVE_TOLERANCE, timeout=MOVE_TIMEOUT)
        logger.info(f'MAPTEST path {done}/{len(MOVE_PATH)} -> {done == len(MOVE_PATH)}')
        self.log_state('after')

    def map_bounds_check(self) -> None:
        logger.hr('Map bounds check')
        if not self.map_move_ensure_main_page():
            logger.error('Not on main page, cannot probe bounds')
            return
        start = self.map_move_read_pos()
        logger.info(f'MAPTEST bounds start {start}')
        extremes = {}
        for direction, key in (((1, 0), 'max_x'), ((-1, 0), 'min_x'),
                               ((0, -1), 'min_y'), ((0, 1), 'max_y')):
            pos = self.map_move_read_pos()
            self.map_hold_direction(direction, BOUNDS_HOLD)
            self.device.sleep(self.MOVE_SETTLE)
            after = self.map_move_read_pos()
            extremes[key] = after
            logger.info(f'MAPTEST bounds {key}: {pos} -> {after}')
        logger.info(f'MAPTEST BOUNDS RESULT: {extremes}')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
