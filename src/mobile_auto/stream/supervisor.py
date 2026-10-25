"""scrcpy server 保活与回收:每进程、每设备一个监督器(P2 工单 01)。

统一驱动截图通道与 viewer 预览通道的生命周期,替代原先散落在
DeviceSession 与 viewer 通道管理器里的两套保活逻辑:引用计数保活
(有用户时在线,最后一个用户离开后空闲计时自停)、损坏回收与退避
重建、显式关闭。成功启动的通道登记进模块级活跃表,解释器正常退出
时由 atexit 兜底关停;硬终止后的设备端残留由启动失败自愈路径(工单
02)兜底,此处不重复。

server 启动属设备写操作:监督器不管租约,由注入的 starter 负责在正确
的租约纪律下创建并启动通道(viewer 经短事务获取/释放;驱动在设备
队列线程内租约已持有时直启)。通道错误仍是能力降级信号,不触碰会话
状态机。
"""

from __future__ import annotations

import atexit
import threading
import time
from collections.abc import Callable
from typing import Protocol

IDLE_STOP_SECONDS = 30.0  # 无用户后 server 自停
RETRY_BACKOFF_INIT = 2.0
RETRY_BACKOFF_MAX = 30.0
_EXIT_STOP_LOCK_TIMEOUT = 2.0  # atexit 限时取锁:守护线程冻结在锁上时放弃


class _Channel(Protocol):
    def healthy(self) -> bool: ...

    def stop(self) -> None: ...


_ACTIVE: set[ServerSupervisor] = set()
_ACTIVE_LOCK = threading.Lock()


def _stop_all_atexit() -> None:
    """解释器退出兜底:尽力关停本进程启动的全部 server。

    取锁限时:守护线程可能冻结在监督器锁中间,拿不到就跳过该通道——
    进程退出会关闭 adb 客户端 socket,设备端 app_process 随流关闭退出。
    """
    with _ACTIVE_LOCK:
        supervisors = list(_ACTIVE)
    for supervisor in supervisors:
        supervisor._stop_for_exit()


atexit.register(_stop_all_atexit)


class ServerSupervisor:
    """单台设备的 scrcpy server 监督器;starter 注入租约纪律,本类管生命周期。

    get() 供轮询方(viewer 预览):尽力而为,失败/退避窗口返回 None;
    require() 供设备队列线程(驱动截图):不健康即重建,启动失败如实抛出。
    两者的失败都推进同一退避,避免坏通道被高频重试。
    """

    def __init__(
        self,
        *,
        starter: Callable[[], _Channel],
        idle_stop: float = IDLE_STOP_SECONDS,
        retry_init: float = RETRY_BACKOFF_INIT,
        retry_max: float = RETRY_BACKOFF_MAX,
    ) -> None:
        self._starter = starter
        self._idle_stop = idle_stop
        self._retry_init = retry_init
        self._retry_max = retry_max
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()  # get() 的启动串行化(require 由队列串行保证)
        self._channel: _Channel | None = None
        self._users = 0
        self._idle_timer: threading.Timer | None = None
        self._next_retry = 0.0
        self._backoff = retry_init
        self._last_error: str | None = None

    # ── 用户引用计数 ─────────────────────────────────────────

    @property
    def user_count(self) -> int:
        """当前注册用户数;用于连接上限等判定。"""
        with self._lock:
            return self._users

    def acquire_user(self) -> None:
        """注册用户:在线通道不为空闲自停;无通道时仅计数。"""
        with self._lock:
            self._users += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

    def release_user(self) -> None:
        """注销用户;最后一个离开后启动空闲自停计时。"""
        with self._lock:
            self._users = max(0, self._users - 1)
            if self._users == 0 and self._idle_timer is None and self._channel is not None:
                self._idle_timer = threading.Timer(self._idle_stop, self._stop_idle)
                self._idle_timer.daemon = True
                self._idle_timer.start()

    # ── 通道获取 ─────────────────────────────────────────────

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def get(self) -> _Channel | None:
        """可用通道;不健康即回收并安排退避,未到期/启动失败返回 None。"""
        with self._lock:
            if self._channel is not None:
                if self._channel.healthy():
                    return self._channel
                self._invalidate_locked("scrcpy 通道不健康(连接断开或泵线程停止)")
                return None
            if time.monotonic() < self._next_retry:
                return None
        if not self._start_lock.acquire(blocking=False):
            return None  # 另一方正在启动,本轮先回退
        try:
            return self._start_channel()
        except Exception:
            return None  # 失败详情已记录并推进退避
        finally:
            self._start_lock.release()

    def require(self) -> _Channel:
        """可用通道,不健康则回收重建;启动失败如实抛出。

        须在设备队列线程内(或同等串行保证下)调用——启动不经
        _start_lock,依赖调用方串行。
        """
        with self._lock:
            if self._channel is not None:
                if self._channel.healthy():
                    return self._channel
                self._stop_channel_locked()  # 静默回收:驱动路径由上层计失败
        return self._start_channel()

    def peek_healthy(self) -> _Channel | None:
        """已存在且健康的通道;不启动、不回收(证据取证等尽力场景)。"""
        with self._lock:
            if self._channel is not None and self._channel.healthy():
                return self._channel
            return None

    def invalidate(self, reason: str) -> None:
        """通道损坏(空帧/连接断):回收并安排退避重试;本轮回退 u2。"""
        with self._lock:
            self._invalidate_locked(reason)

    def shutdown(self) -> None:
        """显式回收:停计时与通道;幂等。"""
        with self._lock:
            self._cancel_timer_locked()
            self._stop_channel_locked()
            self._users = 0

    # ── 内部 ─────────────────────────────────────────────────

    def _start_channel(self) -> _Channel:
        """经 starter 启动并登记;失败记录错误、推进退避后如实抛出。"""
        try:
            channel = self._starter()
        except Exception as exc:
            with self._lock:
                self._schedule_retry_locked(f"视频流启动失败:{exc}")
            raise
        with self._lock:
            self._channel = channel
            self._last_error = None
            self._backoff = self._retry_init
        with _ACTIVE_LOCK:
            _ACTIVE.add(self)
        return channel

    def _invalidate_locked(self, reason: str) -> None:
        """回收当前通道并安排退避;须持 self._lock。"""
        self._stop_channel_locked()
        self._schedule_retry_locked(reason)

    def _schedule_retry_locked(self, reason: str) -> None:
        """记录错误并推进退避窗口;须持 self._lock。"""
        self._last_error = reason
        self._next_retry = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, self._retry_max)

    def _stop_channel_locked(self) -> None:
        """停当前通道(幂等);须持 self._lock。"""
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
            self._channel = None
        with _ACTIVE_LOCK:
            _ACTIVE.discard(self)

    def _cancel_timer_locked(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _stop_idle(self) -> None:
        with self._lock:
            self._cancel_timer_locked()
            if self._users == 0 and self._channel is not None:
                self._stop_channel_locked()
                self._backoff = self._retry_init

    def _stop_for_exit(self) -> None:
        """atexit 路径:限时取锁后关停,拿不到锁就交给进程退出兜底。"""
        if not self._lock.acquire(timeout=_EXIT_STOP_LOCK_TIMEOUT):
            return
        try:
            self._cancel_timer_locked()
            self._stop_channel_locked()
        finally:
            self._lock.release()


__all__ = ["ServerSupervisor"]
