"""设备会话:设备队列、跨进程租约、状态与所有权(plan 7.1/7.2/7.3)。

每个 DeviceSession 持有一个单 worker 线程池,所有设备操作经队列串行执行。
状态机:NEW → READY(已连接)→ LEASED(持有租约)→ DRAINING(关闭中)
→ CLOSED;无法确认在途操作结果进入 FAULTED。start() 默认获取租约进入
LEASED;网页等短事务场景可用 release_lease()/acquire_lease() 在
READY/LEASED 间切换。LEASED 才代表可以向设备发送操作。

取消语义(plan 7.1):每个操作携带取消标记——排队未开始的任务可取消;
运行中的操作在进入设备发送阶段前经 send_checkpoint 做最后取消检查,
已取消则不发送;发送一旦开始,取消只作用于等待方,操作结果记为 unknown,
不自动重放。挂起故障影响范围为宿主进程(决定 Q1):FAULTED 后拒绝
后续设备操作,由操作者确认设备与 App 状态后重启进程恢复。
"""

from __future__ import annotations

import enum
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from ..config import MobileConfig
from ..errors import (
    DeviceBusy,
    DeviceUnavailable,
    SessionFaulted,
)
from .backend import DeviceBackend, U2Backend, enumerate_device_states
from .lease import DeviceLease

T = TypeVar("T")

_CLOSE_DRAIN_TIMEOUT_SECONDS = 10.0
_SCRCPY_FAIL_LIMIT = 3  # 连续失败次数达到后降级 u2(plan P1 工单 03)
_SCRCPY_DEGRADE_SECONDS = 60.0  # 降级持续时长,到期再试一次以探测恢复


class SessionState(enum.StrEnum):
    NEW = "new"
    READY = "ready"
    LEASED = "leased"
    DRAINING = "draining"
    CLOSED = "closed"
    FAULTED = "faulted"


class _OperationCancelled(Exception):
    """内部错误:操作在进入发送阶段前被取消;future 持有它,等待方已取消。"""


class _OpState:
    """单个排队操作的取消与发送阶段记录。"""

    __slots__ = ("cancelled", "entered_send")

    def __init__(self) -> None:
        self.cancelled = False
        self.entered_send = False


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
    """与一台 Android 设备的连接及调度关系;持有串行操作队列与设备租约。"""

    def __init__(
        self,
        config: MobileConfig,
        *,
        backend_factory: Callable[[], DeviceBackend] | None = None,
        device_enumerator: Callable[[], dict[str, str]] | None = None,
        lease_factory: Callable[[str], DeviceLease | object] | None = None,
        channel_factory: Callable[[str], object] | None = None,
    ) -> None:
        self._config = config
        self._backend_factory = backend_factory or U2Backend
        self._enumerator = device_enumerator or enumerate_device_states
        self._lease_factory = lease_factory  # None → 真实 DeviceLease(统一目录)
        self._channel_factory = channel_factory  # None → 真实 ScrcpyChannel(P1)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mobile-auto-device"
        )
        self._lock = threading.Lock()
        self._drain = threading.Condition()
        self._state = SessionState.NEW
        self._serial: str | None = None
        self._backend: DeviceBackend | None = None
        self._lease = None
        self._pending = 0  # 已提交未完成(排队+运行)的操作数
        self._op_states: dict[Future, _OpState] = {}
        self._outcomes: dict[Future, str] = {}  # 完成操作的固化结局
        self._lease_held = False
        self._tls = threading.local()
        # scrcpy 截图通道状态(P1 工单 03):仅设备队列线程读写,无需加锁
        self._scrcpy_channel = None
        self._scrcpy_failures = 0
        self._scrcpy_degraded_until = 0.0

    @property
    def config(self) -> MobileConfig:
        return self._config

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def serial(self) -> str | None:
        return self._serial

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> None:
        """枚举设备、解析序列号、获取租约并建立连接;失败不进入 LEASED。"""
        with self._lock:
            if self._state != SessionState.NEW:
                raise RuntimeError(f"会话已启动,当前状态 {self._state.value}")
            states = self._enumerator()
            serial = resolve_serial(states, self._config.device_serial)
        lease = (
            self._lease_factory(serial)
            if self._lease_factory is not None
            else DeviceLease(serial)
        )
        lease.acquire()  # 被占用 → DeviceBusy,不连接、不排队
        try:
            backend = self._backend_factory()
            backend.connect(serial)
        except Exception as exc:
            lease.release()
            if isinstance(exc, DeviceUnavailable):
                raise
            raise DeviceUnavailable(f"连接设备 {serial} 失败:{exc}") from exc
        with self._lock:
            self._lease = lease
            self._lease_held = True
            self._backend = backend
            self._serial = serial
            self._state = SessionState.LEASED

    def acquire_lease(self) -> None:
        """READY → LEASED;被占用立即报 DeviceBusy,状态保持 READY。"""
        with self._lock:
            if self._state == SessionState.LEASED:
                raise DeviceBusy("设备租约已持有;采集事务不可重入")
            if self._state != SessionState.READY:
                raise RuntimeError(
                    f"仅 READY 状态可获取租约,当前 {self._state.value}"
                )
        self._lease.acquire()
        with self._lock:
            self._lease_held = True
            self._state = SessionState.LEASED

    def release_lease(self) -> None:
        """LEASED → READY;释放不回滚已发送的操作。"""
        with self._lock:
            if self._state != SessionState.LEASED:
                raise RuntimeError(
                    f"仅 LEASED 状态可释放租约,当前 {self._state.value}"
                )
        self._lease.release()
        with self._lock:
            self._lease_held = False
            self._state = SessionState.READY

    # ── scrcpy 截图通道(P1 工单 03)─────────────────────────

    def get_or_start_channel(self):
        """取可用通道,不健康则重建;须在设备队列线程内调用(租约已持有,
        server 推送与启动属于设备写操作)。"""
        if self._scrcpy_channel is not None and not self._scrcpy_channel.healthy():
            self._scrcpy_channel.stop()
            self._scrcpy_channel = None
        if self._scrcpy_channel is None:
            from ..stream.channel import ScrcpyChannel

            factory = self._channel_factory or (
                lambda serial: ScrcpyChannel(serial)
            )
            channel = factory(self._serial)
            channel.start()
            self._scrcpy_channel = channel
        return self._scrcpy_channel

    def screenshot_bytes(self, backend) -> bytes:
        """驱动截图双通道策略(P1 工单 03);在设备队列线程内调用。

        u2=强制 u2;scrcpy=强制 scrcpy 且故障不静默回退;auto=scrcpy 优先,
        连续失败降级 u2 一段时间后重试探测恢复。返回始终是 PNG 字节,
        调用方不感知来源。
        """
        source = self._config.screenshot_source
        if source == "u2":
            return backend.screenshot_png()
        if (
            source == "auto"
            and self._scrcpy_degraded_until > time.monotonic()
        ):
            return backend.screenshot_png()
        try:
            png = self.get_or_start_channel().capture_png()
        except Exception:
            self._scrcpy_failures += 1
            if self._scrcpy_failures >= _SCRCPY_FAIL_LIMIT:
                self._scrcpy_degraded_until = (
                    time.monotonic() + _SCRCPY_DEGRADE_SECONDS
                )
            if source == "scrcpy":
                raise  # 显式选择:故障如实报错,不静默换通道
            return backend.screenshot_png()
        self._scrcpy_failures = 0
        self._scrcpy_degraded_until = 0.0
        return png

    def healthy_channel(self):
        """已存在且健康的 scrcpy 通道;不主动启动(证据取证等尽力场景)。"""
        channel = self._scrcpy_channel
        if channel is not None and channel.healthy():
            return channel
        return None

    def close(self, timeout: float = _CLOSE_DRAIN_TIMEOUT_SECONDS) -> None:
        """有界排空后关闭;幂等。超时进入 FAULTED 并保留租约(plan 7.3)。

        close 超时不等于进程可退出:仍在运行的线程可能延迟解释器退出,
        但进程退出会释放主机锁;已进入设备的请求无法撤销。
        """
        with self._lock:
            if self._state == SessionState.CLOSED:
                return
            if self._state not in (
                SessionState.NEW,
                SessionState.READY,
                SessionState.LEASED,
                SessionState.FAULTED,
            ):
                raise RuntimeError(f"状态 {self._state.value} 不能关闭")
            self._state = SessionState.DRAINING
            backend = self._backend
            self._backend = None
        # 排队未开始的任务可取消;运行中的在途调用结束前不算排空
        with self._lock:
            futures = list(self._op_states)
        for future in futures:
            future.cancel()
        deadline = time.monotonic() + timeout
        with self._drain:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    with self._lock:
                        self._state = SessionState.FAULTED
                    raise SessionFaulted(
                        f"close 排空超时({timeout} 秒),在途操作结果未知;"
                        "会话保持 FAULTED 与设备租约,拒绝后续设备操作;"
                        "请确认设备与 App 状态后重启宿主进程"
                    )
                self._drain.wait(remaining)
        if backend is not None:
            backend.close()
        if self._scrcpy_channel is not None:
            try:
                self._scrcpy_channel.stop()
            except Exception:
                pass
            self._scrcpy_channel = None
        if self._lease is not None and self._lease_held:
            self._lease.release()
            self._lease_held = False
        self._lease = None
        self._executor.shutdown(wait=True)
        with self._lock:
            self._state = SessionState.CLOSED

    # ── 操作队列与取消 ───────────────────────────────────────

    def submit(self, operation: Callable[[DeviceBackend], T]) -> Future[T]:
        """把设备操作排入队列;排队未开始的任务可取消。"""
        with self._lock:
            if self._state == SessionState.CLOSED:
                raise SessionFaulted("会话已关闭,拒绝后续设备操作")
            if self._state == SessionState.FAULTED:
                raise SessionFaulted("会话故障中,拒绝后续设备操作;请重启宿主进程")
            if self._state == SessionState.DRAINING:
                raise SessionFaulted("会话正在关闭,拒绝新操作")
            if self._backend is None:
                raise SessionFaulted(
                    f"会话未就绪(状态 {self._state.value});请先调用 start()"
                )
            backend = self._backend
            self._pending += 1
        op_state = _OpState()

        def wrapped(worker_backend):
            if op_state.cancelled:  # 入口取消检查:排队期间被取消的任务不执行
                raise _OperationCancelled()
            self._tls.op_state = op_state
            try:
                return operation(worker_backend)
            finally:
                self._tls.op_state = None

        future = self._executor.submit(wrapped, backend)
        with self._lock:
            self._op_states[future] = op_state
        future.add_done_callback(self._operation_done)  # 已完成的任务会立即在当前线程回调
        return future

    def send_checkpoint(self) -> None:
        """设备发送阶段检查点(plan 7.1):必须在 worker 线程内、真实设备
        调用发出之前调用;已取消则不发送。仅通过检查才记为进入发送阶段。"""
        op_state = getattr(self._tls, "op_state", None)
        if op_state is None:
            return
        if op_state.cancelled:
            raise _OperationCancelled()  # 取消在发送前生效,不进入发送阶段
        op_state.entered_send = True

    def mark_cancelled(self, future: Future) -> str:
        """等待方被取消时调用:排队未开始的任务取消;已运行的记结果未知。"""
        with self._lock:
            op_state = self._op_states.get(future)
            if op_state is None:
                return "finished"  # 已完成,无需记录
            op_state.cancelled = True
        future.cancel()  # 未开始则取消;已开始无效果
        if op_state.entered_send:
            return "unknown"  # 已进入发送阶段:设备侧结果未知
        return "cancelled-before-send" if future.cancelled() else "unknown"

    def operation_outcome(self, future: Future) -> str:
        """查询操作结局:cancelled-queued / cancelled-before-send / unknown /
        completed / failed / running。完成后的结局已固化,等待方是否感知
        不改变记录(plan 7.1:已发送后取消 = unknown)。"""
        with self._lock:
            op_state = self._op_states.get(future)
            final = self._outcomes.get(future)
        if final is not None:
            return final
        if future.cancelled():
            return "cancelled-queued"
        if op_state is None:
            return "failed" if future.exception() is not None else "completed"
        if op_state.cancelled and op_state.entered_send:
            return "unknown"
        if op_state.cancelled:
            return "cancelled-before-send"
        return "running"

    def _operation_done(self, future: Future) -> None:
        with self._lock:
            op_state = self._op_states.pop(future, None)
            if op_state is not None and not future.cancelled():
                if op_state.cancelled and op_state.entered_send:
                    self._outcomes[future] = "unknown"
                elif op_state.cancelled:
                    self._outcomes[future] = "cancelled-before-send"
                elif future.exception() is not None:
                    self._outcomes[future] = "failed"
                else:
                    self._outcomes[future] = "completed"
        with self._drain:
            self._pending -= 1
            self._drain.notify_all()


__all__ = ["DeviceSession", "SessionState", "resolve_serial"]
