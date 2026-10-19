# This Python file uses the following encoding: utf-8
"""EmulatorExtras 截图后端（移植自 MaaFramework）。

- LDPlayer 9: 调用 ldopengl64.dll 的 CreateScreenShotInstance 直接读取宿主渲染帧
- MuMu 12: 调用 external_renderer_ipc.dll 的 nemu_capture_display

只移植截图，不包含 MuMu 的 IPC 触摸输入；两者都返回项目约定的 RGB 图像。
"""
import ctypes
import os
import re
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np

from module.base.decorator import cached_property, del_cached_property
from module.exception import ScriptError
from module.logger import logger

IS_WINDOWS = os.name == 'nt'
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_WinDLL = getattr(ctypes, 'WinDLL', None)


class _LDPlayerShot:
    """LDPlayer 9 的 IScreenShotClass 封装（vtable: [0]=dtor, [1]=cap, [2]=release）。"""

    def __init__(self, dll_path: Path, index: int, pid: int, width: int, height: int):
        self.width = width
        self.height = height
        self._dll_dir = None
        add_dll_dir = getattr(os, 'add_dll_directory', None)
        if add_dll_dir is not None:
            self._dll_dir = add_dll_dir(str(dll_path.parent))
        self._dll = _WinDLL(str(dll_path))
        create = self._dll.CreateScreenShotInstance
        create.restype = ctypes.c_void_p
        create.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self._obj = create(index, pid)
        if not self._obj:
            raise ScriptError(f'LDPlayer CreateScreenShotInstance 失败: index={index}, pid={pid}')
        vtable = ctypes.cast(
            self._obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        self._cap = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vtable[1])
        self._release = ctypes.WINFUNCTYPE(None, ctypes.c_void_p)(vtable[2])

    def cap(self) -> np.ndarray:
        buffer = self._cap(self._obj)
        if not buffer:
            raise ScriptError('LDPlayer extras cap 返回空指针')
        data = ctypes.string_at(buffer, self.width * self.height * 3)
        # GL_BGR + bottom-up
        image = np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3)
        image = cv2.flip(image, 0)
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        if self._obj:
            self._release(self._obj)
            self._obj = None
        if self._dll_dir is not None:
            self._dll_dir.close()
            self._dll_dir = None


class _MuMuShot:
    """MuMu 12 external_renderer_ipc 截图封装。"""

    def __init__(self, dll_path: Path, emulator_root: Path, index: int, package: str):
        self._dll_dir = None
        add_dll_dir = getattr(os, 'add_dll_directory', None)
        if add_dll_dir is not None:
            self._dll_dir = add_dll_dir(str(dll_path.parent))
        self._dll = _WinDLL(str(dll_path))

        self._connect = self._dll.nemu_connect
        self._connect.restype = ctypes.c_int
        self._connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        self._disconnect = self._dll.nemu_disconnect
        self._disconnect.restype = None
        self._disconnect.argtypes = [ctypes.c_int]
        self._get_display_id = self._dll.nemu_get_display_id
        self._get_display_id.restype = ctypes.c_int
        self._get_display_id.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self._capture = self._dll.nemu_capture_display
        self._capture.restype = ctypes.c_int
        self._capture.argtypes = [
            ctypes.c_int, ctypes.c_uint, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_void_p,
        ]

        self._handle = self._connect(str(emulator_root), index)
        if self._handle <= 0:
            raise ScriptError(
                f'MuMu nemu_connect 失败: path={emulator_root}, index={index}, handle={self._handle}'
            )
        self._display_id = self._resolve_display_id(package)
        self._width = 0
        self._height = 0
        self._buffer = None
        self._init_buffer()

    def _resolve_display_id(self, package: str) -> int:
        if package:
            display_id = self._get_display_id(self._handle, package.encode('utf-8'), 0)
            if display_id >= 0:
                return display_id
        display_id = self._get_display_id(self._handle, b'default', 0)
        return display_id if display_id >= 0 else 0

    def _init_buffer(self) -> None:
        width = ctypes.c_int(0)
        height = ctypes.c_int(0)
        ret = self._capture(self._handle, self._display_id, 0,
                            ctypes.byref(width), ctypes.byref(height), None)
        if ret != 0 or width.value <= 0 or height.value <= 0:
            raise ScriptError(
                f'MuMu capture_display 取分辨率失败: ret={ret}, {width.value}x{height.value}'
            )
        self._width = width.value
        self._height = height.value
        self._buffer = ctypes.create_string_buffer(self._width * self._height * 4)

    def cap(self) -> np.ndarray:
        width = ctypes.c_int(self._width)
        height = ctypes.c_int(self._height)
        ret = self._capture(
            self._handle, self._display_id, len(self._buffer),
            ctypes.byref(width), ctypes.byref(height),
            ctypes.cast(self._buffer, ctypes.c_void_p),
        )
        if ret != 0:
            # 分辨率变化时重新取一次尺寸
            self._init_buffer()
            width = ctypes.c_int(self._width)
            height = ctypes.c_int(self._height)
            ret = self._capture(
                self._handle, self._display_id, len(self._buffer),
                ctypes.byref(width), ctypes.byref(height),
                ctypes.cast(self._buffer, ctypes.c_void_p),
            )
        if ret != 0:
            raise ScriptError(f'MuMu capture_display 失败: ret={ret}')
        image = np.frombuffer(self._buffer.raw, np.uint8)
        image = image.reshape(height.value, width.value, 4)
        image = cv2.flip(image, 0)
        return cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)

    def close(self) -> None:
        if self._handle:
            self._disconnect(self._handle)
            self._handle = 0
        if self._dll_dir is not None:
            self._dll_dir.close()
            self._dll_dir = None


def _ld_registry_roots():
    if not IS_WINDOWS:
        return
    import winreg
    keys = (
        r'SOFTWARE\leidian\ldplayer9',
        r'SOFTWARE\leidian\ldplayer',
        r'SOFTWARE\WOW6432Node\leidian\ldplayer9',
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key in keys:
            try:
                with winreg.OpenKey(hive, key) as reg:
                    yield Path(winreg.QueryValueEx(reg, 'InstallDir')[0])
            except (FileNotFoundError, OSError):
                continue


def _find_ld_root(path: str) -> Path:
    candidates = []
    if path:
        p = Path(path)
        candidates.append(p if p.is_dir() else p.parent)
    candidates.extend(_ld_registry_roots())
    for candidate in candidates:
        if (candidate / 'ldopengl64.dll').exists() and (candidate / 'ldconsole.exe').exists():
            return candidate
    raise ScriptError(
        f'找不到 LDPlayer 9 安装目录（ldopengl64.dll / ldconsole.exe）: {path or "未配置"}'
    )


def _find_mumu_root_and_dll(path: str):
    candidates = []
    if path:
        p = Path(path)
        base = p if p.is_dir() else p.parent
        candidates.extend([base, base.parent, base.parent.parent])
    for candidate in candidates:
        for sub in (candidate / 'nx_main' / 'sdk', candidate / 'shell' / 'sdk'):
            dll = sub / 'external_renderer_ipc.dll'
            if dll.exists():
                return candidate, dll
    raise ScriptError(
        f'找不到 MuMu external_renderer_ipc.dll: {path or "未配置"}'
    )


def _parse_ld_index(name: str, serial: str) -> int:
    match = re.search(r'leidian(\d+)', name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r'127\.0\.0\.1:(\d+)', serial)
    if match:
        port = int(match.group(1))
        if 5555 <= port <= 5555 + 64:
            return (port - 5555) // 2
    return 0


def _parse_mumu_index(name: str) -> int:
    match = re.search(r'MuMuPlayer(?:Global)?-(?:12|15)\.0-(\d+)', name)
    return int(match.group(1)) if match else 0


def _ld_list2(console: Path):
    result = subprocess.run(
        [str(console), 'list2'],
        capture_output=True,
        timeout=8,
        creationflags=_CREATE_NO_WINDOW,
    )
    text = result.stdout.decode('gbk', errors='ignore')
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(',')
        if len(parts) < 9:
            continue
        try:
            rows.append({
                'index': int(parts[0]),
                'pid': int(parts[-5]),
                'width': int(parts[-3]),
                'height': int(parts[-2]),
            })
        except ValueError:
            continue
    return rows


class EmulatorExtras:
    """Screenshot 混入类：EmulatorExtras 截图方式。"""

    _extras_lock = threading.RLock()

    @property
    def emulator_extras_supported(self) -> bool:
        try:
            _ = self._extras_backend
            return True
        except Exception as exc:  # noqa: BLE001
            logger.info(f'EmulatorExtras 不可用: {exc}')
            return False

    @cached_property
    def _extras_backend(self):
        if not IS_WINDOWS or _WinDLL is None:
            raise ScriptError('EmulatorExtras 仅支持 Windows')

        device = self.config.script.device
        emulator_type = str(device.emulatorinfo_type)
        name = str(device.emulatorinfo_name)
        path = str(device.emulatorinfo_path)
        joined = f'{emulator_type} {name} {path}'.lower()

        if 'ldplayer' in joined or 'leidian' in joined:
            return self._create_ld_backend(name, path)
        if 'mumu' in joined:
            return self._create_mumu_backend(name, path)
        try:
            return self._create_ld_backend(name, path)
        except Exception:
            return self._create_mumu_backend(name, path)

    def _create_ld_backend(self, name: str, path: str):
        root = _find_ld_root(path)
        index = _parse_ld_index(name, str(self.config.script.device.serial))
        rows = _ld_list2(root / 'ldconsole.exe')
        row = next((item for item in rows if item['index'] == index), None)
        if row is None:
            raise ScriptError(f'LDPlayer 实例 {index} 未运行（ldconsole list2 无此实例）')
        pid = row['pid']
        width, height = row['width'], row['height']
        if width <= 0 or height <= 0:
            width, height = self._query_resolution()
        logger.info(
            f'[EmulatorExtras] LDPlayer idx={index}, pid={pid}, '
            f'{width}x{height}, dll={root / "ldopengl64.dll"}'
        )
        return _LDPlayerShot(root / 'ldopengl64.dll', index, pid, width, height)

    def _create_mumu_backend(self, name: str, path: str):
        root, dll = _find_mumu_root_and_dll(path)
        index = _parse_mumu_index(name)
        package = str(getattr(self, 'package', '') or '')
        if package == 'auto':
            package = ''
        logger.info(
            f'[EmulatorExtras] MuMu idx={index}, package={package or "default"}, '
            f'root={root}, dll={dll}'
        )
        return _MuMuShot(dll, root, index, package)

    def _query_resolution(self):
        match = re.findall(r'(\d+)x(\d+)', self.adb_shell(['wm', 'size']))
        if not match:
            raise ScriptError('无法获取设备分辨率（wm size）')
        return int(match[-1][0]), int(match[-1][1])

    def screenshot_emulator_extras(self) -> np.ndarray:
        with self._extras_lock:
            try:
                return self._extras_backend.cap()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f'EmulatorExtras 截图失败: {exc}，重建实例')
                self.release_emulator_extras()
                return self._extras_backend.cap()

    def release_emulator_extras(self) -> None:
        with self._extras_lock:
            backend = self.__dict__.get('_extras_backend')
            if backend is not None:
                try:
                    backend.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f'EmulatorExtras 释放失败: {exc}')
            del_cached_property(self, '_extras_backend')
