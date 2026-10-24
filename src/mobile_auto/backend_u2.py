"""u2/ADB 的有限调用(plan 10)。

DeviceBackend / BackendElement 是执行核心依赖的设备接口形状;
U2Backend 是 uiautomator2 的实现。ADB 设备枚举经 adb 二进制的只读
`adb devices` 输出,是 doctor 与会话共用的唯一实现。
uiautomator2 与 adbutils 一律延迟导入,保证包导入无设备副作用。
首次真实连接会触发 u2 设备端组件初始化,这是显式建立会话的一部分;
doctor 不执行这些调用。
"""

from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contracts import Locator
from .errors import DeviceUnavailable

_ADB_TIMEOUT_SECONDS = 10


class AdbProbeError(RuntimeError):
    """doctor 专有的 adb 探测失败,不属于驱动操作错误类别(plan 5.4)。

    不会逸出 doctor:该错误总被转为报告中的问题项。
    """


@dataclass(frozen=True)
class DeviceEntry:
    serial: str
    state: str  # adb devices 输出的原始状态:device / unauthorized / offline / ...

    @property
    def authorized(self) -> bool:
        return self.state == "device"


def find_adb_binary() -> str | None:
    """ADB 可见性:优先 uiautomator2 附带的 adbutils 内置二进制,其次 PATH。"""
    try:
        import adbutils

        path = adbutils.adb_path()
    except (ImportError, AttributeError, OSError, RuntimeError):
        path = None
    if path and Path(path).exists():
        return str(path)
    return shutil.which("adb")


def parse_adb_devices(output: str) -> list[DeviceEntry]:
    """解析 `adb devices` 输出;空行、表头与守护进程提示行忽略。"""
    entries = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            entries.append(DeviceEntry(serial=parts[0], state=parts[1]))
    return entries


def list_adb_devices(adb_path: str, runner=subprocess.run) -> list[DeviceEntry]:
    """对设备的唯一动作:`adb devices`;不写入、不安装、不初始化。"""
    try:
        proc = runner(
            [adb_path, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_ADB_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdbProbeError(f"无法执行 adb({exc})") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise AdbProbeError(f"adb devices 退出码 {proc.returncode}{suffix}")
    return parse_adb_devices(proc.stdout)


def enumerate_device_states() -> dict[str, str]:
    """枚举 adb 可见设备及状态,含 unauthorized/offline;供会话设备选择使用。"""
    adb_path = find_adb_binary()
    if adb_path is None:
        raise DeviceUnavailable("未找到 adb(应随 uiautomator2 的 adbutils 分发,或在 PATH 中)")
    try:
        entries = list_adb_devices(adb_path)
    except AdbProbeError as exc:
        raise DeviceUnavailable(f"adb 探测失败:{exc}") from exc
    return {entry.serial: entry.state for entry in entries}


class BackendElement(Protocol):
    """设备端一个已匹配节点;由后端实现,核心只依赖此形状。"""

    @property
    def bounds(self) -> tuple[int, int, int, int] | None: ...

    @property
    def text(self) -> str: ...

    def click(self) -> None: ...


class DeviceBackend(Protocol):
    """执行核心对设备的全部请求面;伪造实现见 tests/unit/conftest.py。"""

    def connect(self, serial: str) -> None: ...

    def close(self) -> None: ...

    def screen_size(self) -> tuple[int, int]: ...

    def find_elements(self, by: Locator, value: str) -> list[BackendElement]: ...

    def screenshot_png(self) -> bytes: ...

    def app_start(self, package: str, activity: str | None, stop: bool = False) -> None: ...


class U2Backend:
    """基于 uiautomator2 的设备后端;每个方法对应一次有限的底层调用。"""

    def __init__(self) -> None:
        self._device = None

    def connect(self, serial: str) -> None:
        import uiautomator2 as u2

        try:
            self._device = u2.connect(serial)
        except Exception as exc:  # u2 连接失败路径众多,统一归为设备不可用
            raise DeviceUnavailable(f"连接设备 {serial} 失败:{exc}") from exc

    def close(self) -> None:
        # 仅释放主机侧引用;不停止 App、不杀外部 adb、不卸载设备端组件。
        self._device = None

    def screen_size(self) -> tuple[int, int]:
        size = self._device.window_size()
        return int(size[0]), int(size[1])

    def find_elements(self, by: Locator, value: str) -> list[BackendElement]:
        selector = self._build_selector(by, value)
        return [_U2Element(selector[i]) for i in range(selector.count)]

    def screenshot_png(self) -> bytes:
        image = self._device.screenshot()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def app_start(self, package: str, activity: str | None, stop: bool = False) -> None:
        self._device.app_start(package, activity, stop=stop)

    def _build_selector(self, by: Locator, value: str):
        if by is Locator.RESOURCE_ID:
            return self._device(resourceId=value)
        if by is Locator.TEXT:
            return self._device(text=value)
        raise ValueError(f"U2Backend 未实现的定位方式:{by}")  # 调用方已先经 selectors 校验


class _U2Element:
    """把 u2 UiObject 包装为本包 BackendElement 形状,隔离 u2 API 变化。

    text/bounds 取自 find 时的一次 info 快照,与查询句柄语义一致;
    click 仍作用于设备上的活节点。
    """

    def __init__(self, element) -> None:
        self._element = element
        try:
            info = element.info
        except Exception:  # 节点在快照前消失:按无 bounds 处理,由可见性过滤剔除
            info = {}
        self._info = info or {}

    @property
    def bounds(self) -> tuple[int, int, int, int] | None:
        bounds = self._info.get("bounds") or {}
        try:
            return (
                int(bounds["left"]),
                int(bounds["top"]),
                int(bounds["right"]),
                int(bounds["bottom"]),
            )
        except (KeyError, TypeError):
            return None

    @property
    def text(self) -> str:
        return self._info.get("text") or ""

    def click(self) -> None:
        self._element.click()
