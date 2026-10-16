# This Python file uses the following encoding: utf-8
"""
网络实时坐标读取（0x066E 位置同步解析）

从设备的 TCP 流中实时解析游戏服务器的 0x066E 高频同步消息，得到场景内所有实体的
实时坐标（**引擎坐标系**，与右上角显示的"地点 x,y"存在平移关系，需按地图校准，
见 tests/probe_net_position.py）。

原理（2026-10-14 实机抓包逆向，消息格式见《明珠轩辕_技能数据库》/分析报告 12.1）：
- 服务器 → 客户端帧：`55 43 [len:1B] [msgid:2B大端] [payload]`
- `0x066E` 位置同步（引擎原生、高频）：位置帧 12B = `[实体:4B][0002][0000][x:2B][y:2B]`
- `0x15AF` 名字广播：`[实体:4B][标志:2B][名字 UTF-16BE]`（用于实体→名字映射）

数据来源：设备端 tcpdump 流式输出（`tcpdump -w -`），需设备有 root（LDPlayer 自带）。

用法：
    from module.map.net_position import NetPositionReader
    reader = NetPositionReader(device)          # device = module.device.device.Device
    reader.start()                              # 启动（后台线程解析）
    ...
    reader.get_position('f007d7e3')             # -> (x, y) | None
    reader.get_entities_by_name('情话至深')      # -> {'f007d7e3': (x, y), ...}
    reader.get_named_entities()                 # -> {'情话至深': {...}, ...}
    reader.stop()
"""
import struct
import subprocess
import threading
import time
import typing as t

from module.logger import logger

# 服务器端口（明珠轩辕战斗/同步服务器）
GAME_PORT = 18011
# 帧头
FRAME_MAGIC = b'\x55\x43'
# 消息ID
MID_POSITION = 0x066E
MID_NAME = 0x15AF
# 位置帧布局：[实体:4][类型:2][保留:2][x:2][y:2]
POSITION_TYPE = 0x0002
POSITION_LEN = 12


class _Entity:
    __slots__ = ('entity', 'name', 'x', 'y', 'ts')

    def __init__(self, entity: str):
        self.entity = entity
        self.name: t.Optional[str] = None
        self.x: t.Optional[int] = None
        self.y: t.Optional[int] = None
        self.ts: float = 0.0


class NetPositionReader:
    """
    网络实时坐标读取器（线程安全）。

    数据流：adb shell → su → tcpdump -w -（pcap 流）→ 解析 TCP 载荷 → 游戏帧 → 实体坐标/名字。
    """

    def __init__(self, device=None, adb_path: t.Optional[str] = None,
                 serial: t.Optional[str] = None, port: int = GAME_PORT):
        """
        :param device: 框架 Device 对象（用于取 adb 路径与序列号，可省略）
        :param adb_path: adb 可执行文件路径（省略时从 device/环境推断）
        :param serial: 设备序列号（省略时从 device 推断）
        :param port: 服务器端口（默认 18011）
        """
        self.device = device
        self.port = port
        self._adb = adb_path or self._guess_adb(device)
        self._serial = serial or self._guess_serial(device)

        self._proc: t.Optional[subprocess.Popen] = None
        self._thread: t.Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._entities: t.Dict[str, _Entity] = {}
        self._buffer = bytearray()
        self._error: t.Optional[str] = None

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        """启动解析（后台线程）。返回是否成功拉起 tcpdump。"""
        if self._running:
            return True
        cmd = self._build_cmd()
        logger.info(f'[NetPos] start: {" ".join(cmd)}')
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, shell=False,
            )
        except Exception as e:
            self._error = str(e)
            logger.error(f'[NetPos] failed to start tcpdump: {e}')
            return False
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, name='NetPos', daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """停止解析并杀掉 tcpdump。"""
        self._running = False
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        logger.info('[NetPos] stopped')

    @property
    def error(self) -> t.Optional[str]:
        return self._error

    # ------------------------------------------------------------------ 查询接口
    def get_position(self, entity: str) -> t.Optional[t.Tuple[int, int]]:
        """取指定实体的最新坐标（实体ID为 8 位十六进制小写字符串，如 'f007d7e3'）"""
        with self._lock:
            e = self._entities.get(entity.lower())
            if e is None or e.x is None:
                return None
            return e.x, e.y

    def get_entities_by_name(self, name: str) -> t.Dict[str, t.Tuple[int, int]]:
        """取所有该名字的实体坐标（同一角色可能有多个历史实体ID）"""
        out = {}
        with self._lock:
            for e in self._entities.values():
                if e.name == name and e.x is not None:
                    out[e.entity] = (e.x, e.y)
        return out

    def get_named_entities(self) -> t.Dict[str, t.Dict[str, t.Tuple[int, int]]]:
        """全部有名实体：{名字: {实体: (x, y)}}"""
        out: t.Dict[str, t.Dict[str, t.Tuple[int, int]]] = {}
        with self._lock:
            for e in self._entities.values():
                if e.name and e.x is not None:
                    out.setdefault(e.name, {})[e.entity] = (e.x, e.y)
        return out

    def get_entity_info(self, entity: str) -> t.Optional[dict]:
        """取实体的完整信息（名字/坐标/更新时间）"""
        with self._lock:
            e = self._entities.get(entity.lower())
            if e is None:
                return None
            return {'entity': e.entity, 'name': e.name, 'x': e.x, 'y': e.y, 'ts': e.ts}

    def is_fresh(self, entity: str, within: float = 10.0) -> bool:
        """该实体坐标是否为近期更新（秒）"""
        with self._lock:
            e = self._entities.get(entity.lower())
            return e is not None and (time.time() - e.ts) <= within

    # ------------------------------------------------------------------ 内部：命令
    def _guess_adb(self, device) -> str:
        # 框架 Device（adbutils）通常带 adb 路径（adb_binary）；退化到 PATH 里的 adb
        for attr in ('adb_binary', 'adb_path', 'adb'):
            v = getattr(device, attr, None)
            if isinstance(v, str) and v:
                return self._resolve_adb(v)
        return 'adb'

    @staticmethod
    def _resolve_adb(path: str) -> str:
        """相对路径（如 ./bin/adb/adb.exe）按项目根解析"""
        import os
        if os.path.isabs(path) or os.path.exists(path):
            return path
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidate = os.path.join(root, path.lstrip('./'))
        if os.path.exists(candidate):
            return candidate
        return path

    def _guess_serial(self, device) -> t.Optional[str]:
        for attr in ('serial', 'device_serial'):
            v = getattr(device, attr, None)
            if isinstance(v, str) and v:
                return v
        return None

    def _build_cmd(self) -> t.List[str]:
        inner = f'/system/xbin/tcpdump -i any -s 0 -w - tcp port {self.port}'
        cmd = [self._adb]
        if self._serial:
            cmd += ['-s', self._serial]
        cmd += ['shell', f'su -c "{inner}"']
        return cmd

    # ------------------------------------------------------------------ 内部：解析
    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        # 跳过 pcap 全局头（24B）
        header = self._read_exact(proc.stdout, 24)
        if len(header) < 24:
            self._error = 'no pcap header (tcpdump 未启动？设备无 root？)'
            logger.error(f'[NetPos] {self._error}')
            return
        logger.info(f'[NetPos] pcap stream started (magic={header[:4].hex()})')
        while self._running:
            rec = self._read_exact(proc.stdout, 16)
            if len(rec) < 16:
                break
            _, _, incl_len, _ = struct.unpack('<IIII', rec)
            if incl_len <= 0 or incl_len > 65535:
                break
            data = self._read_exact(proc.stdout, incl_len)
            if len(data) < incl_len:
                break
            payload = self._extract_tcp_payload(data)
            if payload:
                self._buffer += payload
                self._parse_frames()
        if self._running:
            logger.warning('[NetPos] tcpdump stream ended unexpectedly')

    @staticmethod
    def _read_exact(stream, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = stream.read(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return bytes(buf)

    def _extract_tcp_payload(self, pkt: bytes) -> t.Optional[bytes]:
        """从 SLL 抓包记录里取出服务器→客户端的 TCP 载荷"""
        if len(pkt) < 16 + 20:
            return None
        ip = pkt[16:]  # 跳过 Linux cooked(SLL) 头
        if ip[0] >> 4 != 4 or ip[9] != 6:
            return None
        ihl = (ip[0] & 0xF) * 4
        if len(ip) < ihl + 20:
            return None
        src_port = struct.unpack_from('>H', ip, ihl)[0]
        if src_port != self.port:
            return None
        tcp = ip[ihl:]
        doff = (tcp[12] >> 4) * 4
        if len(tcp) <= doff:
            return None
        return tcp[doff:]

    def _parse_frames(self) -> None:
        buf = self._buffer
        i = 0
        n = len(buf)
        while i + 5 <= n:
            if buf[i] == 0x55 and buf[i + 1] == 0x43:
                length = buf[i + 2]
                if 5 <= length <= 600 and i + length <= n:
                    mid = struct.unpack_from('>H', buf, i + 3)[0]
                    payload = bytes(buf[i + 5:i + length])
                    if mid == MID_POSITION:
                        self._on_position(payload)
                    elif mid == MID_NAME:
                        self._on_name(payload)
                    i += length
                    continue
            i += 1
        if i:
            del buf[:i]
        # 防止无限增长
        if len(buf) > 4096:
            del buf[:-1024]

    def _on_position(self, p: bytes) -> None:
        if len(p) != POSITION_LEN:
            return
        entity = p[0:4].hex()
        ptype, _res, x, y = struct.unpack_from('>HHHH', p, 4)
        if ptype != POSITION_TYPE:
            return
        with self._lock:
            e = self._entities.get(entity)
            if e is None:
                e = self._entities[entity] = _Entity(entity)
            e.x, e.y = x, y
            e.ts = time.time()

    def _on_name(self, p: bytes) -> None:
        if len(p) < 8:
            return
        entity = p[0:4].hex()
        rest = p[6:]
        chars = []
        j = 0
        while j + 1 < len(rest):
            ch = (rest[j] << 8) | rest[j + 1]
            if ch == 0:
                break
            if 0x20 <= ch < 0x7f or 0x4e00 <= ch <= 0x9fff:
                chars.append(chr(ch))
                j += 2
            else:
                break
        if not chars:
            return
        name = ''.join(chars)
        with self._lock:
            e = self._entities.get(entity)
            if e is None:
                e = self._entities[entity] = _Entity(entity)
            e.name = name
