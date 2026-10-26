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
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
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
# 坐标基准校验间隔:注入前对比缓存尺寸与实时 wm size 并刷新的最小周期
_COORDINATE_BASIS_INTERVAL_SECONDS = 1.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_screen_size(source) -> tuple[int, int] | None:
    """读取尺寸;通道/后端不可用时返回 None(保鲜不得新增注入失败点)。"""
    getter = getattr(source, "screen_size", None)
    if getter is None:
        return None
    try:
        width, height = getter()
    except Exception:
        return None
    return int(width), int(height)


def _can_refresh(channel) -> bool:
    """通道是否真的能重探基准;未声明时以是否实现刷新方法为准。"""
    flag = getattr(channel, "can_refresh_screen_size", None)
    if flag is not None:
        return bool(flag)
    return callable(getattr(channel, "refresh_screen_size", None))


def _same_aspect(first: tuple[int, int], second: tuple[int, int]) -> bool:
    """宽高比一致(容忍 1% 取整误差):视频缩放后的同一显示区。"""
    aw, ah = first
    bw, bh = second
    if not ah or not bh:
        return False
    left = aw * bh
    right = bw * ah
    return abs(left - right) <= 0.01 * max(left, right, 1)


def _basis_matches(
    channel_size: tuple[int, int],
    backend_size: tuple[int, int],
    refreshable: bool,
) -> bool:
    """通道基准是否可用于本次注入。

    可刷新通道要求数值一致(wm size 同源);不可刷新通道允许视频缩放,
    只要求宽高比一致。
    """
    if channel_size == backend_size:
        return True
    return not refreshable and _same_aspect(channel_size, backend_size)


def _copy_basis(basis: dict) -> dict:
    copied = dict(basis)
    for key in ("channel", "backend"):
        value = copied.get(key)
        copied[key] = list(value) if value else None
    return copied


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
        # 设备密度(工单 02):会话内缓存,`wm density` 为权威来源
        self._density: int | None = None
        self._density_loaded = False
        self._pending = 0  # 已提交未完成(排队+运行)的操作数
        self._op_states: dict[Future, _OpState] = {}
        self._outcomes: dict[Future, str] = {}  # 完成操作的固化结局
        self._lease_held = False
        self._tls = threading.local()
        # scrcpy 截图通道(P1 工单 03 / P2 工单 01):监督器仅设备队列线程读写
        self._channel_supervisor = None
        self._scrcpy_failures = 0
        self._scrcpy_degraded_until = 0.0
        # 弹窗守卫取证统计:适配器在预算耗尽时记录尝试与命中,证据原样带上;
        # 只读快照对外暴露,写入用独立锁(读取方来自 pytest 线程)
        self._dialog_guard_lock = threading.Lock()
        self._dialog_guard_stats = {"attempts": 0, "dismissed": [], "last_at": None}
        # 动作流水(工单 01):会话标识、注入通道快照与 Journal 句柄。
        # 适配器装饰器每次公共调用前 begin_operation() 清空通道,调用后读取,
        # 避免把上一步的通道记到这一步(读取方来自 pytest/事件循环线程)。
        self._session_id = uuid.uuid4().hex[:8]
        self._injection_lock = threading.Lock()
        self._last_injection_channel: str | None = None
        self._last_coordinate_basis: dict | None = None
        self._last_cancel_after_send: bool | None = None
        self._journal = None
        # 坐标基准保鲜(工单 04):缓存按通道对象失效,仅在设备队列线程读写
        self._basis_channel = None
        self._basis_checked_at = 0.0
        self._basis_cached: dict | None = None

    @property
    def config(self) -> MobileConfig:
        return self._config

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def serial(self) -> str | None:
        return self._serial

    # ── 设备密度(工单 02)──────────────────────────────────────

    def density(self, backend: DeviceBackend) -> int | None:
        """取会话缓存的设备密度;尚未读取时读一次 `wm density`。

        必须在设备队列线程内调用(会访问后端)。读取失败返回 None 且不
        缓存,下一次调用重试。会话内缓存的依据:一次匹配事务内密度不会
        变化;设备密度被 `wm density` 覆盖后由调用方显式 refresh_density。
        """
        with self._lock:
            if self._density_loaded:
                return self._density
        return self.refresh_density(backend)

    def refresh_density(self, backend: DeviceBackend) -> int | None:
        """重读 `wm density` 并刷新缓存;viewer 每次采集都调用(真实调用点)。

        失败返回 None 并保留上一次成功值(不清空已知密度);本次匹配由
        调用方退化为旧路径。清理时机:会话关闭后缓存随会话丢弃。
        """
        try:
            value = backend.screen_density()
        except Exception:  # 密度是可选加速信息:失败不报错,退化为旧行为
            return None
        if value is None:
            return None
        with self._lock:
            self._density = value
            self._density_loaded = True
        return value

    @property
    def dialog_guard_stats(self) -> dict:
        """弹窗守卫的只读统计:attempts 为清理尝试次数(含未命中),
        dismissed 为历次命中的目标值,last_at 为最后一次尝试的 epoch 秒。"""
        with self._dialog_guard_lock:
            return {
                "attempts": self._dialog_guard_stats["attempts"],
                "dismissed": list(self._dialog_guard_stats["dismissed"]),
                "last_at": self._dialog_guard_stats["last_at"],
            }

    def note_dialog_guard_attempt(self, dismissed: list[str]) -> None:
        """登记一次清理尝试;由适配器在队列任务返回后调用。"""
        with self._dialog_guard_lock:
            self._dialog_guard_stats["attempts"] += 1
            self._dialog_guard_stats["dismissed"].extend(dismissed)
            self._dialog_guard_stats["last_at"] = time.time()

    @property
    def session_id(self) -> str:
        """构造时生成的 8 位随机标识,动作流水按它归属同一次会话。"""
        return self._session_id

    @property
    def journal(self):
        """动作流水句柄;未挂接(未开启证据)时为 None,适配器零开销。"""
        return self._journal

    def set_journal(self, journal) -> None:
        """挂接动作流水;生命周期归调用方(关闭由挂接方负责)。"""
        self._journal = journal

    def begin_operation(self) -> None:
        """公共操作开始:清空上一次的注入通道、坐标基准与取消阶段快照。"""
        with self._injection_lock:
            self._last_injection_channel = None
            self._last_coordinate_basis = None
            self._last_cancel_after_send = None

    @property
    def last_cancel_after_send(self) -> bool | None:
        """最近一次等待方取消是否发生在发送阶段;未登记取消为 None。"""
        with self._injection_lock:
            return self._last_cancel_after_send

    @property
    def last_injection_channel(self) -> str | None:
        """最近一次成功后实际使用的注入通道("scrcpy"/"u2");加锁只读快照。"""
        with self._injection_lock:
            return self._last_injection_channel

    @property
    def last_coordinate_basis(self) -> dict | None:
        """最近一次注入前的坐标基准快照;未注入过(或 u2 路径)为 None。"""
        with self._injection_lock:
            basis = self._last_coordinate_basis
        return None if basis is None else _copy_basis(basis)

    def _note_injection_channel(self, channel: str) -> str:
        with self._injection_lock:
            self._last_injection_channel = channel
        return channel

    def _note_coordinate_basis(self, basis: dict) -> None:
        with self._injection_lock:
            self._last_coordinate_basis = basis

    def ensure_coordinate_basis(self, backend, channel) -> dict:
        """注入前校验并保鲜坐标基准;必须在设备队列线程内调用。

        比较通道缓存的屏幕尺寸与后端实时尺寸(u2 ``window_size`` 即
        ``wm size``)。不一致且通道可刷新时调用 ``refresh_screen_size()``
        并重读,返回本次实际使用的基准;同会话同通道对象 1 秒内最多
        校验一次(含一次 ``wm size`` 往返),通道对象变化(监督器重建)
        立即重校验。

        上游通道(``stream_backend="upstream"``)的 ``refresh_screen_size``
        是空实现,且 ``screen_size`` 来自视频会话元包(可能被 max_size
        缩放、旋转时由泵线程自动更新)。因此对不可刷新通道只比较宽高比:
        比例一致视为同一显示区的视频缩放;比例不一致如实记录
        ``refreshed=False, refreshable=False, consistent=False``,不调用
        空实现伪装刷新成功,也不改道注入通道。

        只观测与保鲜:通道/后端读尺寸失败分别记 None,不抛出新的失败;
        刷新失败在基准里记 ``refresh_error`` 后继续注入。
        """
        now = time.monotonic()
        cached = self._basis_cached
        if (
            cached is not None
            and self._basis_channel is channel
            and now - self._basis_checked_at < _COORDINATE_BASIS_INTERVAL_SECONDS
        ):
            self._note_coordinate_basis(cached)
            return _copy_basis(cached)

        channel_size = _safe_screen_size(channel)
        backend_size = _safe_screen_size(backend)
        refreshable = _can_refresh(channel)
        refreshed = False
        refresh_error = None
        consistent: bool | None = None
        if channel_size is not None and backend_size is not None:
            consistent = _basis_matches(channel_size, backend_size, refreshable)
            if not consistent and refreshable:
                try:
                    channel.refresh_screen_size()
                except Exception as exc:  # 刷新失败不阻断注入,基准如实降级
                    refresh_error = type(exc).__name__
                else:
                    new_size = _safe_screen_size(channel)
                    if new_size is not None:
                        refreshed = new_size != channel_size
                        channel_size = new_size
                consistent = _basis_matches(channel_size, backend_size, refreshable)

        basis = {
            "channel": list(channel_size) if channel_size else None,
            "backend": list(backend_size) if backend_size else None,
            "refreshed": refreshed,
            "refreshable": refreshable,
            "consistent": consistent,
            "checked_at": _utc_now(),
        }
        if refresh_error is not None:
            basis["refresh_error"] = refresh_error
        self._basis_channel = channel
        self._basis_checked_at = now
        self._basis_cached = basis
        self._note_coordinate_basis(basis)
        return _copy_basis(basis)

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
        """READY → LEASED;被占用立即报 DeviceBusy,状态保持 READY。

        状态迁移与设备锁获取在同一把会话锁内完成:检查、占位与 OS 锁
        获取原子,并发调用方在锁上即看到 LEASED,不会出现双持有者导致
        的二次获取。设备锁为非阻塞获取(失败立即 DeviceBusy),锁内
        执行不会长时间占住会话锁。
        """
        with self._lock:
            if self._state == SessionState.LEASED:
                raise DeviceBusy("设备租约已持有;采集事务不可重入")
            if self._state != SessionState.READY:
                raise RuntimeError(
                    f"仅 READY 状态可获取租约,当前 {self._state.value}"
                )
            self._lease.acquire()  # 非阻塞;失败报 DeviceBusy,状态不变
            self._state = SessionState.LEASED
            self._lease_held = True

    def release_lease(self) -> None:
        """LEASED → READY;释放不回滚已发送的操作。

        状态迁移与设备锁释放在同一把会话锁内完成:先改状态后放锁的
        窗口里,重新获取会因同进程已持锁而空转复用旧锁,随后被前一次
        释放放掉——会话自认持锁而互斥已失。双重释放也在此显式报错,
        而不是落到设备锁层后以竞态方式表现。
        """
        with self._lock:
            if self._state != SessionState.LEASED:
                raise RuntimeError(
                    f"仅 LEASED 状态可释放租约,当前 {self._state.value}"
                )
            self._lease.release()
            self._state = SessionState.READY
            self._lease_held = False

    # ── scrcpy 截图通道(P1 工单 03)─────────────────────────

    def get_or_start_channel(self):
        """取可用通道,不健康则重建;须在设备队列线程内调用(租约已持有,
        server 推送与启动属于设备写操作)。生命周期语义由 ServerSupervisor
        提供(P2 工单 01):首用注册会话级用户,通道伴随会话、不空闲自停,
        close 时随 shutdown 关停。"""
        supervisor = self._channel_supervisor
        if supervisor is None:
            from ..stream import channel_factory as make_channel_factory
            from ..stream.supervisor import ServerSupervisor

            factory = make_channel_factory(
                self._config.stream_backend,
                channel_factory_override=self._channel_factory,
            )
            supervisor = ServerSupervisor(
                starter=lambda: self._start_channel(factory)
            )
            supervisor.acquire_user()  # 会话级用户:close 时随 shutdown 注销
            self._channel_supervisor = supervisor
        return supervisor.require()

    def _start_channel(self, factory):
        channel = factory(self._serial)
        channel.start()
        return channel

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
            self._note_channel_failure()
            if source == "scrcpy":
                raise  # 显式选择:故障如实报错,不静默换通道
            return backend.screenshot_png()
        self._note_channel_success()
        return png

    def healthy_channel(self):
        """已存在且健康的 scrcpy 通道;不主动启动、不回收(证据取证等尽力场景)。"""
        supervisor = self._channel_supervisor
        if supervisor is None:
            return None
        return supervisor.peek_healthy()

    def _record_u2_basis(self, backend) -> None:
        """显式 u2 注入的坐标基准观测(工单 04 §04a)。

        通道已存在时与 scrcpy 路径同样校验尺寸并记录基准;没有可用通道时
        **不为校验而启动通道**,如实记 ``channel=null``/``refreshable=false``/
        ``consistent=false`` 与原因。注入通道本身绝不因此改道。
        """
        channel = self.healthy_channel()
        if channel is not None:
            self.ensure_coordinate_basis(backend, channel)
            return
        backend_size = _safe_screen_size(backend)
        self._note_coordinate_basis(
            {
                "channel": None,
                "backend": list(backend_size) if backend_size else None,
                "refreshed": False,
                "refreshable": False,
                "consistent": False,
                "checked_at": _utc_now(),
                "reason": "显式 u2 注入;无可用视频通道,不启动通道校验基准",
            }
        )

    # ── 注入双通道(scroll/图像点击发送/press_key 与 adapter.device 共用)──

    def inject_gesture(self, backend, source, scrcpy_gesture, u2_gesture) -> str:
        """双通道注入执行;设备队列线程内调用。

        source:auto=取配置 injection_source;scrcpy=强制且故障如实报错;
        u2=直接走 u2。与 screenshot_bytes 共用失败计数与降级窗口(同一
        scrcpy 通道,一个健康状态):连续失败达阈值后 auto 在窗口内直接
        走 u2,窗口到期再试以探测恢复。

        手势安全由手势闭包保证(device_ops.gesture 实现):首写(DOWN)
        前失败报 _CleanFallback → 整体回退 u2;DOWN 后失败闭包内已抬指
        收尾,此处如实传播不重放(避免双注入)。

        坐标基准:实际走 scrcpy 通道(含显式 ``source="scrcpy"``)时,首写
        之前调用 :meth:`ensure_coordinate_basis` 校验/刷新并记录基准;显式
        scrcpy 只校验不改道。显式 ``source="u2"``:已有可用通道时同样校验并
        记录;没有通道时只记录 ``channel=null``/``refreshable=false`` 与原因,
        不为校验而启动通道,也绝不改道。降级窗口内直接走 u2 的路径不探测
        通道(降级原因即通道不可用)。
        """
        from .device_ops import _CleanFallback

        resolved = source if source != "auto" else self._config.injection_source
        if resolved == "u2":
            self._record_u2_basis(backend)
            u2_gesture()
            return self._note_injection_channel("u2")
        if resolved == "auto" and self._scrcpy_degraded_until > time.monotonic():
            u2_gesture()
            return self._note_injection_channel("u2")
        try:
            channel = self.get_or_start_channel()
        except Exception:
            self._note_channel_failure()
            if resolved == "scrcpy":
                raise
            u2_gesture()
            return self._note_injection_channel("u2")
        self.ensure_coordinate_basis(backend, channel)
        try:
            scrcpy_gesture(channel)
        except _CleanFallback as failure:
            self._note_channel_failure()
            if resolved == "scrcpy":
                raise failure.original from None
            u2_gesture()  # DOWN 未发出:整体回退
            return self._note_injection_channel("u2")
        except Exception:
            self._note_channel_failure()
            raise  # DOWN 后失败:手势已抬指收尾,不重放
        self._note_channel_success()
        return self._note_injection_channel("scrcpy")

    def inject_tap(self, backend, x: int, y: int) -> None:
        """坐标单击(图像点击事务的发送路径);通道按配置 injection_source。"""
        from .device_ops import tap_gesture

        self.inject_gesture(
            backend,
            "auto",
            lambda ch: tap_gesture(ch, backend, x, y),
            lambda: backend.tap(x, y),
        )

    def inject_swipe(
        self, backend, sx: int, sy: int, ex: int, ey: int, duration: float
    ) -> None:
        """坐标滑动(契约 scroll 的手势);通道按配置 injection_source。"""
        from .device_ops import swipe_gesture

        self.inject_gesture(
            backend,
            "auto",
            lambda ch: swipe_gesture(ch, backend, sx, sy, ex, ey, duration),
            lambda: backend.swipe(sx, sy, ex, ey, duration),
        )

    def inject_press_key(self, backend, name: str) -> None:
        """按键注入:back/home 双通道(按配置);其余键直接 u2
        (scrcpy 通道仅映射这两个键)。"""
        from .device_ops import press_gesture

        if name not in ("back", "home"):
            backend.press_key(name)
            return
        self.inject_gesture(
            backend,
            "auto",
            lambda ch: press_gesture(ch, name),
            lambda: backend.press_key(name),
        )

    def _note_channel_failure(self) -> None:
        self._scrcpy_failures += 1
        if self._scrcpy_failures >= _SCRCPY_FAIL_LIMIT:
            self._scrcpy_degraded_until = (
                time.monotonic() + _SCRCPY_DEGRADE_SECONDS
            )

    def _note_channel_success(self) -> None:
        self._scrcpy_failures = 0
        self._scrcpy_degraded_until = 0.0

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
        if self._channel_supervisor is not None:
            self._channel_supervisor.shutdown()
            self._channel_supervisor = None
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
        with self._injection_lock:
            self._last_cancel_after_send = op_state.entered_send
        if op_state.entered_send:
            return "unknown"  # 已进入发送阶段:设备侧结果未知
        return "cancelled-before-send"  # 未进入发送:检查点保证不会发送

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
