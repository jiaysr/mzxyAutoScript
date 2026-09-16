# This Python file uses the following encoding: utf-8
"""设备单例持有（内部模块，不注册工具）。"""
import threading

from dev_tools.mcp.errors import McpToolError

_lock = threading.Lock()
_devices: dict = {}


def get_device(config_name: str = "", *, interval: float = 0.2):
    """按配置懒加载 Device（复用 annotator 的构建方式，延迟导入重量级模块）。"""
    from dev_tools.mcp.runtime import runtime

    resolved = runtime.resolve_config(config_name)
    with _lock:
        device = _devices.get(resolved)
        if device is not None:
            return device
        from module.config.config import Config
        from module.device.device import Device

        try:
            config = Config(config_name=resolved)
            device = Device(config=config)
        except Exception as exc:  # noqa: BLE001
            raise McpToolError(
                f"设备连接失败: {exc}",
                hint="确认模拟器已启动、config/<配置>.json 中设备配置正确",
            ) from exc
        device.disable_stuck_detection()
        device.screenshot_interval_set(interval)
        _devices[resolved] = device
        return device


def take_screenshot(config_name: str = ""):
    device = get_device(config_name)
    try:
        return device.screenshot()
    except Exception as exc:  # noqa: BLE001
        raise McpToolError(f"截图失败: {exc}", hint="确认模拟器已启动且在运行") from exc


def release_all() -> None:
    for device in list(_devices.values()):
        try:
            device.release_during_wait()
        except Exception:  # noqa: BLE001
            pass
    _devices.clear()
