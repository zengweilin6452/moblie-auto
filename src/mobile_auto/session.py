"""设备会话:设备队列、状态与所有权(plan 7.1)。

每个 DeviceSession 持有一个单 worker 线程池,所有设备操作经队列串行执行。
状态机完整集合为 NEW → READY → LEASED → DRAINING → READY、CLOSED、FAULTED;
LEASED/DRAINING/FAULTED 的转换随租约与取消工单(04)交付,当前仅走
NEW → READY → CLOSED。租约(跨进程独占)同样属于工单 04。

设备选择(GLOSSARY“设备会话”):拥有会话不等于独占设备;序列号来源为
配置,空序列号仅允许唯一已授权在线设备,任何歧义都报错而非代选。
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from .backend_u2 import DeviceBackend, U2Backend, enumerate_device_states
from .config import MobileConfig
from .errors import DeviceUnavailable, SessionFaulted

T = TypeVar("T")


class SessionState(enum.StrEnum):
    NEW = "new"
    READY = "ready"
    LEASED = "leased"
    DRAINING = "draining"
    CLOSED = "closed"
    FAULTED = "faulted"


def resolve_serial(states: dict[str, str], configured: str | None) -> str:
    """按配置解析目标设备序列号;任何歧义或不匹配都明确报错。"""
    authorized = sorted(s for s, state in states.items() if state == "device")
    if configured:
        state = states.get(configured)
        if state is None:
            raise DeviceUnavailable(
                f"配置的设备 {configured} 未连接(在线设备:{', '.join(authorized) or '无'})"
            )
        if state == "unauthorized":
            raise DeviceUnavailable(
                f"设备 {configured} 未授权;请在手机上确认 USB 调试授权"
            )
        if state != "device":
            raise DeviceUnavailable(f"设备 {configured} 状态异常:{state}")
        return configured
    if not states:
        raise DeviceUnavailable("未发现任何设备;请连接设备后重试")
    if not authorized:
        unauthorized = sorted(
            s for s, state in states.items() if state == "unauthorized"
        )
        if unauthorized:
            raise DeviceUnavailable(
                "发现设备但均未授权:" + ", ".join(unauthorized)
                + ";请在手机上确认 USB 调试授权"
            )
        detail = ", ".join(f"{s}={states[s]}" for s in sorted(states))
        raise DeviceUnavailable(f"发现设备但均不在线或状态异常:{detail}")
    if len(authorized) > 1:
        raise DeviceUnavailable(
            "多台已授权设备在线且未配置 MOBILE_DEVICE_SERIAL:"
            + ", ".join(authorized)
            + ";请在配置中指定设备序列号"
        )
    return authorized[0]


class DeviceSession:
    """与一台 Android 设备的连接及调度关系;持有串行操作队列。"""

    def __init__(
        self,
        config: MobileConfig,
        *,
        backend_factory: Callable[[], DeviceBackend] | None = None,
        device_enumerator: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._config = config
        self._backend_factory = backend_factory or U2Backend
        self._enumerator = device_enumerator or enumerate_device_states
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mobile-auto-device"
        )
        self._lock = threading.Lock()
        self._state = SessionState.NEW
        self._serial: str | None = None
        self._backend: DeviceBackend | None = None

    @property
    def config(self) -> MobileConfig:
        return self._config

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def serial(self) -> str | None:
        return self._serial

    def start(self) -> None:
        """枚举设备、解析序列号并建立连接;失败不进入 READY。"""
        with self._lock:
            if self._state != SessionState.NEW:
                raise RuntimeError(f"会话已启动,当前状态 {self._state.value}")
            states = self._enumerator()
            serial = resolve_serial(states, self._config.device_serial)
        backend = self._backend_factory()
        try:
            backend.connect(serial)
        except Exception as exc:
            if isinstance(exc, DeviceUnavailable):
                raise
            raise DeviceUnavailable(f"连接设备 {serial} 失败:{exc}") from exc
        with self._lock:
            self._backend = backend
            self._serial = serial
            self._state = SessionState.READY

    def submit(self, operation: Callable[[DeviceBackend], T]) -> Future[T]:
        """把设备操作排入队列;排队未开始的任务可被取消(工单 04 深化)。"""
        with self._lock:
            if self._state == SessionState.CLOSED:
                raise SessionFaulted("会话已关闭,拒绝后续设备操作")
            if self._state == SessionState.FAULTED:
                raise SessionFaulted("会话故障中,拒绝后续设备操作;请重启宿主进程")
            if self._backend is None:
                raise SessionFaulted(
                    f"会话未就绪(状态 {self._state.value});请先调用 start()"
                )
            backend = self._backend
        return self._executor.submit(operation, backend)

    def close(self) -> None:
        """幂等关闭:不再接受新操作;不停止 App,不杀外部 adb。"""
        with self._lock:
            if self._state == SessionState.CLOSED:
                return
            self._state = SessionState.CLOSED
            backend = self._backend
            self._backend = None
        if backend is not None:
            backend.close()
        self._executor.shutdown(wait=True)


__all__ = ["DeviceSession", "SessionState", "resolve_serial"]
