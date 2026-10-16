# This Python file uses the following encoding: utf-8
"""
网络坐标探针：实时打印实体坐标；以及"网络坐标 ↔ 右上角显示坐标"的校准。

用法：
    $env:PYTHONPATH="D:\project\mzxy\mzxyAutoScript"
    toolkit\python.exe tests\probe_net_position.py             # live：实时打印有名实体坐标
    toolkit\python.exe tests\probe_net_position.py calibrate   # 校准：读OCR显示坐标+网络坐标，走动20秒

校准原理：
    显示坐标 = 网络坐标 − 地图原点（按地图平移，比例 1:1 待验证）
    让角色持续走动时，只有"真身"实体满足：原点 = 网络坐标 − 显示坐标 恒定不变；
    其余实体（历史分身/宠物/别的玩家）算出的原点会乱跳。
    据此可同时得到：1) 你角色的当前实体ID；2) 该地图的原点常量（记到 move.py 或配置里）。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config
from module.device.device import Device
from module.logger import logger
from module.map.net_position import NetPositionReader

# 你的角色名（用于筛实体）
PLAYER_NAME = '情话至深'
# 校准持续时间（秒）
CALIBRATE_DURATION = 20
# 校准采样间隔（秒）
CALIBRATE_INTERVAL = 1.0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'live'
    config = Config('oas1')
    device = Device(config)
    reader = NetPositionReader(device)
    if not reader.start():
        logger.error(f'启动失败: {reader.error}（设备端 tcpdump 需要 root；LDPlayer 默认有）')
        return
    try:
        if mode == 'calibrate':
            calibrate(reader, config, device)
        else:
            live(reader)
    finally:
        reader.stop()


def live(reader: NetPositionReader):
    """实时打印有名实体坐标（Ctrl+C 退出）"""
    logger.info('实时打印中……（走动一下，看哪个实体的坐标在跟着变）')
    while True:
        time.sleep(2)
        named = reader.get_named_entities()
        for name, ents in list(named.items())[:6]:
            pts = '  '.join(f'{e}:({x},{y})' for e, (x, y) in list(ents.items())[:5])
            logger.info(f'[{name}] {pts}')


def calibrate(reader: NetPositionReader, config, device):
    """
    校准：OCR 显示坐标 + 网络坐标 → 找出真身实体与地图原点
    """
    from tasks.GameUi.game_ui import GameUi
    game = GameUi(config, device)

    logger.info(f'校准开始：请让角色【持续走动】约 {CALIBRATE_DURATION} 秒（摇杆/寻路都行）')
    samples = {}   # entity -> [ (origin_x, origin_y) ]
    loc_names = set()
    t0 = time.time()
    while time.time() - t0 < CALIBRATE_DURATION:
        loc = game.map_current_location()
        if loc is not None:
            name, dx, dy = loc
            loc_names.add(name)
            for e, (nx, ny) in reader.get_entities_by_name(PLAYER_NAME).items():
                samples.setdefault(e, []).append((nx - dx, ny - dy))
        else:
            logger.warning('OCR 没读到坐标，跳过本次采样')
        time.sleep(CALIBRATE_INTERVAL)

    if not samples:
        logger.error('没采到样本：检查角色名 PLAYER_NAME 是否正确、网络解析是否正常')
        return
    logger.info(f'地点: {loc_names}')
    logger.info('实体原点候选（= 网络坐标 − 显示坐标；越稳定越可能是真身）：')
    result = []
    for e, origins in sorted(samples.items(), key=lambda kv: -len(kv[1])):
        xs = [o[0] for o in origins]
        ys = [o[1] for o in origins]
        spread = max(max(xs) - min(xs), max(ys) - min(ys))
        result.append((spread, e, (round(sum(xs) / len(xs)), round(sum(ys) / len(ys))), len(origins)))
    for spread, e, origin, n in sorted(result)[:10]:
        flag = '  <== 真身？' if spread <= 3 else ''
        logger.info(f'  {e}  原点≈{origin}  波动={spread}  样本={n}{flag}')
    logger.info('提示：波动最小的那个就是你的角色实体；原点记下来，可换算显示坐标 = 网络坐标 − 原点')


if __name__ == '__main__':
    main()
