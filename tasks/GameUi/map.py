# This Python file uses the following encoding: utf-8
"""
世界地图传送导航（MapNavigation）：

- `map_current_location()`  读取主页面右上角的地点与坐标（如 沼泽427,59）
- `map_open_world_map_list()` / `map_close_world_map_list()` 等页面开关
- `map_teleport(name)`      传送到指定地点（列表点地点 -> 确认弹窗 -> 回主页面）
- `map_ensure_location_initial(name)` 确保角色站在某地点的传送落点上（供跑图任务使用）

页面链路：主页面(右上角小地图) -> 小地图弹窗(世界地图) -> 世界地图(列表) -> 世界地图列表(点地点)
传送确认弹窗有两种（特权免费传送 / 花费铜贝传送），确认按钮位置有偏差，
用 OCR 定位「确定」文字点击，失败再退回公共区域固定点击。

地点初始坐标 MAP_INITIAL_POS 为真机实测的传送落点，用于判断是否在某点位的初始位置。
"""
import re
from difflib import SequenceMatcher
from time import sleep

from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask


class MapNavigation(BaseTask, GameUiAssets):
    """世界地图（小地图 / 大地图 / 列表 / 传送）导航能力"""

    # 所有可传送地点（世界地图列表 = 全部地点 - 当前所在地点）
    MAP_LOCATIONS = ('沼泽', '建木城外', '迟步坡', '苍梧', '寿华', '古碑林',
                     '牧野', '万剑冢', '骨牢', '忘川', '域外灵岛')
    # 各地点的传送落点坐标（传送到该地点后的初始位置，2026-09-22 真机实测）
    MAP_INITIAL_POS = {
        '沼泽': (427, 59),
        '建木城外': (200, 165),
        '迟步坡': (87, 275),
        '苍梧': (62, 50),
        '寿华': (137, 279),
        '古碑林': (70, 112),
        '牧野': (55, 420),
        '万剑冢': (437, 258),
        '骨牢': (163, 480),
        '忘川': (412, 375),
        '域外灵岛': (332, 139),
    }
    # 判定“在同一位置”的坐标容差
    MAP_POS_TOLERANCE = 8
    # 传送前列表页等待时间
    MAP_LIST_SETTLE = 0.8
    # OCR 形近字误识修正表（真机实测）：把识别结果里的错误片段替换成正确名称
    # 「万剑冢」的冢常被识别成家（字形相近，放大也救不了），「域」常被识别成或
    MAP_NAME_FIXES = (
        ('万剑家', '万剑冢'),
        ('或外灵岛', '域外灵岛'),
        # 「岛」常被识别成异体字「島」
        ('域外灵島', '域外灵岛'),
    )

    # ------------------------------------------------------------------ 地点识别
    @classmethod
    def map_fix_name(cls, text: str) -> str:
        """
        修正地点名里的 OCR 形近字误识（如 万剑家 -> 万剑冢）
        """
        if not text:
            return text
        for wrong, right in cls.MAP_NAME_FIXES:
            if wrong in text:
                text = text.replace(wrong, right)
        return text

    @classmethod
    def map_parse_location(cls, text: str):
        """
        解析地点文字（如 沼泽427,59 / 古碑林70,112），并修正形近字误识
        :return: (地点名, x, y)；解析失败返回 None
        """
        if not text:
            return None
        match = re.search(r'(\d{1,3})\s*[,，.．:：]\s*(\d{1,3})', text)
        if not match:
            return None
        name = re.sub(r'[^\u4e00-\u9fa5]', '', text[:match.start()])
        name = cls.map_fix_name(name)
        if not name:
            return None
        return name, int(match.group(1)), int(match.group(2))

    def map_current_location(self):
        """
        读取主页面右上角当前地点与坐标
        :return: (地点名, x, y)；识别失败返回 None
        """
        self.screenshot()
        results = self.O_MAP_LOCATION.detect_and_ocr(self.device.image, logDisplay=False)
        text = ''.join(item.ocr_text for item in results)
        location = self.map_parse_location(text)
        logger.attr('Map current location', f'{text} -> {location}')
        return location

    def map_in_location(self, name: str) -> bool:
        """当前是否在指定地点（只看地点名，不看坐标）"""
        location = self.map_current_location()
        if location is None:
            return False
        return self.map_name_match(location[0], name)

    def map_in_location_initial(self, name: str, tolerance: int = None) -> bool:
        """
        当前是否在指定地点的传送落点上（地点名一致且坐标等于实测初始坐标）
        未记录初始坐标时按不在处理（会触发传送）
        """
        location = self.map_current_location()
        if location is None or not self.map_name_match(location[0], name):
            return False
        position = self.MAP_INITIAL_POS.get(name)
        if position is None:
            logger.warning(f'No initial position recorded for [{name}]')
            return False
        return self.map_pos_match(location[1], location[2], position, tolerance)

    def map_name_match(self, ocr_text: str, name: str) -> bool:
        """
        地点名匹配（容忍 OCR 形近字误识）
        - 先按 MAP_NAME_FIXES 修正已知形近字（万剑家 -> 万剑冢）
        - 完全包含：直接命中
        - 相似度 >= 0.75：容忍首字误识
        - 相似度 >= 0.6 且前两字一致：容忍其他形近字误识
        地点名来自固定列表，相互之间差异大，不会误判
        """
        if not ocr_text or not name:
            return False
        candidate = self.map_fix_name(ocr_text)
        if name in candidate:
            return True
        if len(name) < 2 or len(candidate) < 2:
            return False
        window = candidate[:len(name)]
        ratio = SequenceMatcher(None, window, name).ratio()
        if ratio >= 0.75:
            return True
        return ratio >= 0.6 and window[:2] == name[:2]

    def map_pos_match(self, x: int, y: int, position: tuple, tolerance: int = None) -> bool:
        """坐标是否与记录的初始坐标在同一位置"""
        if tolerance is None:
            tolerance = self.MAP_POS_TOLERANCE
        return abs(x - position[0]) <= tolerance and abs(y - position[1]) <= tolerance

    # ------------------------------------------------------------------ 页面开关
    def map_close_main_popup(self, timeout: float = 6) -> bool:
        """
        关闭主页面上的遮挡弹窗（活动弹窗等）

        活动开启前后游戏会自动弹出活动弹窗，它会盖住右上角地点文字和小地图入口，
        打开地图前必须先关掉（ui_goto 只判断主页面右下角特征，弹窗开着也会认定到达主页面）

        :return: 没有遮挡弹窗返回 True
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_PAGE_ACTIVITY):
                return True
            logger.info('Close activity popup on main page')
            self.appear_then_click(self.I_PAGE_ACTIVITY, action=self.C_ACTIVITY_CLOSE, interval=1)
            sleep(0.3)
        logger.warning('Main page popup does not close')
        return False

    def map_open_minimap(self, timeout: float = 8) -> bool:
        """主页面点击右上角小地图，打开小地图弹窗"""
        logger.info('Open minimap')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_MAP_MINIMAP_WORLD_MAP):
                return True
            self.click(self.C_MAP_MINIMAP_ENTRY, interval=1)
            sleep(0.2)
        logger.warning('Minimap does not appear')
        return False

    def map_close_minimap(self, timeout: float = 8) -> bool:
        """关闭小地图弹窗（回主页面）"""
        logger.info('Close minimap')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_MAP_MINIMAP_WORLD_MAP):
                return True
            self.appear_then_click(self.I_MAP_POPUP_CLOSE, interval=1)
            sleep(0.2)
        logger.warning('Minimap does not close')
        return False

    def map_close_world_map(self, timeout: float = 8) -> bool:
        """关闭世界地图（回主页面）"""
        logger.info('Close world map')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_MAP_WORLD_MAP_LIST_BUTTON):
                return True
            self.click(self.C_MAP_WORLD_MAP_CLOSE, interval=1)
            sleep(0.2)
        logger.warning('World map does not close')
        return False

    def map_close_world_map_list(self, timeout: float = 8) -> bool:
        """关闭世界地图列表（回世界地图）"""
        logger.info('Close world map list')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(self.I_MAP_WORLD_MAP_LIST_PAGE):
                return True
            self.click(self.C_MAP_WORLD_MAP_LIST_CLOSE, interval=1)
            sleep(0.2)
        logger.warning('World map list does not close')
        return False

    def map_open_world_map_list(self, timeout: float = 25) -> bool:
        """
        打开世界地图列表：主页面 -> 小地图 -> 世界地图 -> 列表
        （已经在小地图/世界地图/列表页时会自动续接）
        """
        logger.hr('Open world map list')
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_MAP_WORLD_MAP_LIST_PAGE):
                logger.info('World map list is open')
                self.device.sleep(self.MAP_LIST_SETTLE)
                return True
            if self.appear(self.I_MAP_MINIMAP_WORLD_MAP):
                # 小地图弹窗：点「世界地图」
                self.appear_then_click(self.I_MAP_MINIMAP_WORLD_MAP, interval=1)
            elif self.appear(self.I_MAP_WORLD_MAP_LIST_BUTTON):
                # 世界地图：点「列表」
                self.appear_then_click(self.I_MAP_WORLD_MAP_LIST_BUTTON, interval=1)
            else:
                # 主页面：点右上角小地图入口
                self.click(self.C_MAP_MINIMAP_ENTRY, interval=1)
            sleep(0.3)
        logger.warning('World map list does not appear')
        return False

    # ------------------------------------------------------------------ 传送
    def map_find_list_location(self, name: str):
        """
        在世界地图列表里查找目标地点
        :return: (x, y) 地点名文字中心（可点击）；未找到返回 None
        """
        self.screenshot()
        results = self.O_MAP_WORLD_MAP_LIST.detect_and_ocr(self.device.image, logDisplay=False)
        roi = self.O_MAP_WORLD_MAP_LIST.roi
        for item in results:
            text = item.ocr_text.strip()
            if not self.map_name_match(text, name):
                continue
            box = item.box
            x = (min(point[0] for point in box) + max(point[0] for point in box)) / 2 + roi[0]
            y = (min(point[1] for point in box) + max(point[1] for point in box)) / 2 + roi[1]
            logger.info(f'Teleport target [{text}] found at ({x:.0f}, {y:.0f})')
            return x, y
        logger.warning(f'Teleport target [{name}] not found in world map list')
        return None

    def map_confirm_teleport(self, timeout: float = 8) -> bool:
        """
        等待传送确认弹窗并点击「确定」
        两种弹窗：特权免费传送（两行文案，按钮偏下）/ 花费铜贝传送（一行文案，按钮偏上）
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            self.screenshot()
            results = self.O_MAP_TELEPORT_DIALOG.detect_and_ocr(self.device.image, logDisplay=False)
            texts = [item.ocr_text for item in results]
            if not any('传送' in text for text in texts):
                sleep(0.3)
                continue
            text = ''.join(texts)
            if '铜贝' in text:
                logger.info(f'Paid teleport dialog: [{text}]')
            else:
                logger.info(f'Free teleport dialog: [{text}]')
            # 优先 OCR 定位「确定」按钮（兼容两种弹窗的按钮位置偏差）
            if self.ocr_appear_click(self.O_MAP_DIALOG_CONFIRM):
                logger.info('Click teleport confirm by OCR')
                return True
            # 兜底：两种弹窗「确定」按钮的公共区域
            x, y = self.C_MAP_TELEPORT_CONFIRM.coord()
            logger.info(f'Click teleport confirm fallback at ({x}, {y})')
            self.device.click(x=x, y=y, control_name='map_teleport_confirm')
            self.device.click_record_clear()
            return True
        logger.warning('Teleport confirm dialog does not appear')
        return False

    def map_teleport(self, name: str, timeout: int = 60) -> bool:
        """
        传送到指定地点（主页面/小地图/世界地图/列表页均可发起）
        :return: 传送成功（主页面地点文字已变为目标地点）返回 True
        """
        logger.hr(f'Teleport to {name}')
        if name not in self.MAP_LOCATIONS:
            logger.warning(f'Unknown teleport location [{name}]')
        if not self.map_open_world_map_list():
            return False
        coord = self.map_find_list_location(name)
        if coord is None:
            # 列表不含当前所在地点，已经在该地点则视为成功
            if self.map_in_location(name):
                logger.info(f'Already at [{name}], no teleport needed')
                self.map_close_world_map_list()
                return True
            self.map_close_world_map_list()
            return False
        logger.info(f'Click teleport target [{name}] at ({coord[0]:.0f}, {coord[1]:.0f})')
        self.device.click(x=coord[0], y=coord[1], control_name=f'map_teleport_{name}')
        self.device.click_record_clear()
        # 点地点后列表页自动关闭并弹出确认弹窗
        self.ui_reset_current_page()
        if not self.map_confirm_teleport():
            return False
        # 确认后传送，等待主页面地点文字变成目标地点
        # 这里是纯等待循环（没有点击），需要主动清理卡死记录，否则 60s 会被判定为游戏卡死
        timer = Timer(timeout).start()
        while not timer.reached():
            self.device.stuck_record_clear()
            location = self.map_current_location()
            if location and self.map_name_match(location[0], name):
                logger.info(f'Teleported to [{location[0]}] {location[1]},{location[2]}')
                self.ui_reset_current_page()
                return True
            sleep(0.5)
        logger.warning(f'Teleport to [{name}] timeout')
        return False

    def map_ensure_location_initial(self, name: str) -> bool:
        """
        确保角色站在指定地点的传送落点（初始位置）上：
        - 在目标地点且坐标等于实测初始坐标 -> 不动
        - 在目标地点但不在初始位置 -> 先传送到其他地点，再传送回来
        - 不在目标地点 -> 直接传送
        """
        logger.hr(f'Ensure location [{name}] initial position')
        location = self.map_current_location()
        if location is not None and self.map_name_match(location[0], name):
            position = self.MAP_INITIAL_POS.get(name)
            if position is None:
                logger.warning(f'No initial position recorded for [{name}], skip check')
                return True
            if self.map_pos_match(location[1], location[2], position):
                logger.info(f'Already at [{name}] initial position {location[1]},{location[2]}')
                return True
            relay = self.map_relay_location(name)
            logger.info(f'At [{name}] {location[1]},{location[2]} but not initial '
                        f'{position[0]},{position[1]}, teleport to [{relay}] first')
            if not self.map_teleport(relay):
                return False
        return self.map_teleport(name)

    def map_relay_location(self, name: str) -> str:
        """取一个不同于 name 的中转地点（用于离开当前地点后重新传送回来）"""
        for location in self.MAP_LOCATIONS:
            if location != name:
                return location
        return name
