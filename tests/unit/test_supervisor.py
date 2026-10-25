"""ServerSupervisor 生命周期单元测试(P2 工单 01):注入伪造通道,不触碰 adb。"""

import threading
import time

import pytest

from mobile_auto.stream.supervisor import (
    _ACTIVE,
    ServerSupervisor,
    _stop_all_atexit,
)


class FakeChannel:
    def __init__(self, *, healthy=True, start_error=None):
        self._healthy = healthy
        self.start_error = start_error
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def healthy(self):
        return self._healthy

    def stop(self):
        self.stop_calls += 1
        self._healthy = False


def make_starter():
    """记录创建顺序的 starter 工厂;按需注入每次返回的通道或异常。"""
    created = []

    def start(channel):
        created.append(channel)
        return lambda: (channel.start() or channel)

    return start, created


# ── require(驱动路径)──────────────────────────────────────


def test_require_starts_once_and_reuses():
    starter, created = make_starter()
    channel = FakeChannel()
    supervisor = ServerSupervisor(starter=starter(channel))
    try:
        assert supervisor.require() is channel
        assert supervisor.require() is channel
        assert channel.start_calls == 1
        assert len(created) == 1
    finally:
        supervisor.shutdown()


def test_require_rebuilds_unhealthy_channel():
    _, _ = make_starter()
    dead = FakeChannel(healthy=False)
    fresh = FakeChannel()
    channels = [dead, fresh]
    supervisor = ServerSupervisor(starter=lambda: channels.pop(0))
    try:
        assert supervisor.require() is dead  # 启动后即不健康也如实返回
        assert supervisor.require() is fresh  # 再取:静默回收并重建
        assert dead.stop_calls == 1
    finally:
        supervisor.shutdown()


def test_require_start_failure_raises_and_records_backoff():
    error = RuntimeError("server 连接超时")
    state = {"fail": True, "channel": FakeChannel()}
    attempts = []

    def starter():
        attempts.append(1)
        if state["fail"]:
            raise error
        state["channel"].start()
        return state["channel"]

    supervisor = ServerSupervisor(starter=starter, retry_init=0.05)
    try:
        with pytest.raises(RuntimeError):
            supervisor.require()
        assert "视频流启动失败" in supervisor.last_error
        # 失败进入退避:get() 在窗口内不再尝试
        assert supervisor.get() is None
        assert len(attempts) == 1
        state["fail"] = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and supervisor.get() is None:
            time.sleep(0.02)
        assert supervisor.get() is state["channel"]
        assert supervisor.last_error is None
    finally:
        supervisor.shutdown()


# ── get(viewer 轮询路径)──────────────────────────────────


def test_get_returns_none_on_start_failure_without_raising():
    def starter():
        raise RuntimeError("推送资产失败")

    supervisor = ServerSupervisor(starter=starter)
    try:
        assert supervisor.get() is None
        assert "视频流启动失败" in supervisor.last_error
    finally:
        supervisor.shutdown()


def test_get_unhealthy_channel_recycled_with_backoff():
    dead = FakeChannel(healthy=False)
    fresh = FakeChannel()
    channels = [fresh]
    supervisor = ServerSupervisor(
        starter=lambda: channels.pop(0), retry_init=0.05
    )
    supervisor._channel = dead  # 预置已启动但不健康的通道
    try:
        assert supervisor.get() is None  # 不健康:回收 + 退避,本轮不重建
        assert dead.stop_calls == 1
        assert supervisor.get() is None  # 退避窗口内保持 None
        assert fresh.start_calls == 0
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and supervisor.get() is None:
            time.sleep(0.02)
        assert supervisor.get() is fresh
    finally:
        supervisor.shutdown()


def test_get_during_concurrent_start_returns_none():
    started = threading.Event()
    release = threading.Event()
    channel = FakeChannel()

    def starter():
        started.set()
        release.wait(timeout=5)
        channel.start()
        return channel

    supervisor = ServerSupervisor(starter=starter)
    other = threading.Thread(target=supervisor.get, daemon=True)
    other.start()
    try:
        assert started.wait(timeout=2)
        assert supervisor.get() is None  # 另一方启动中:本轮回退
    finally:
        release.set()
        other.join(timeout=5)
        supervisor.shutdown()


def test_invalidate_stops_channel_and_schedules_retry():
    channel = FakeChannel()
    supervisor = ServerSupervisor(starter=lambda: channel)
    supervisor.require()
    supervisor.invalidate("空帧")
    try:
        assert channel.stop_calls == 1
        assert supervisor.last_error == "空帧"
        assert supervisor.get() is None  # 退避窗口内
    finally:
        supervisor.shutdown()


# ── 用户引用计数与空闲自停 ────────────────────────────────


def test_channel_kept_while_user_registered():
    channel = FakeChannel()
    supervisor = ServerSupervisor(
        starter=lambda: channel, idle_stop=0.05
    )
    supervisor.acquire_user()
    supervisor.require()
    time.sleep(0.2)
    try:
        assert channel.stop_calls == 0  # 有用户:不自停
        assert supervisor.user_count == 1
    finally:
        supervisor.shutdown()


def test_idle_stop_after_last_user_leaves():
    channel = FakeChannel()
    supervisor = ServerSupervisor(
        starter=lambda: channel, idle_stop=0.05
    )
    supervisor.require()
    supervisor.acquire_user()
    supervisor.release_user()  # 最后一个用户离开 → 计时
    time.sleep(0.3)
    try:
        assert channel.stop_calls == 1
        assert supervisor._channel is None
    finally:
        supervisor.shutdown()


def test_idle_stop_requires_all_users_gone():
    channel = FakeChannel()
    supervisor = ServerSupervisor(
        starter=lambda: channel, idle_stop=0.1
    )
    supervisor.require()
    supervisor.acquire_user()
    supervisor.acquire_user()
    supervisor.release_user()
    time.sleep(0.3)
    try:
        assert channel.stop_calls == 0  # 仍有用户
        supervisor.release_user()
        time.sleep(0.3)
        assert channel.stop_calls == 1
    finally:
        supervisor.shutdown()


# ── 关闭与 atexit 兜底 ────────────────────────────────────


def test_shutdown_idempotent_and_resets_users():
    channel = FakeChannel()
    supervisor = ServerSupervisor(starter=lambda: channel)
    supervisor.acquire_user()
    supervisor.require()
    supervisor.shutdown()
    supervisor.shutdown()
    assert channel.stop_calls == 1
    assert supervisor.user_count == 0
    assert supervisor._channel is None


def test_active_registry_tracks_channel_lifetime():
    channel = FakeChannel()
    supervisor = ServerSupervisor(starter=lambda: channel)
    supervisor.require()
    try:
        assert supervisor in _ACTIVE
        supervisor.invalidate("回收")
        assert supervisor not in _ACTIVE
        supervisor.require()
        assert supervisor in _ACTIVE
    finally:
        supervisor.shutdown()
    assert supervisor not in _ACTIVE


def test_stop_all_atexit_stops_active_supervisors():
    channel = FakeChannel()
    supervisor = ServerSupervisor(starter=lambda: channel)
    supervisor.require()
    _stop_all_atexit()
    try:
        assert channel.stop_calls == 1
        assert supervisor not in _ACTIVE
    finally:
        supervisor.shutdown()


def test_stop_all_atexit_skips_locked_supervisor_without_deadlock():
    channel = FakeChannel()
    supervisor = ServerSupervisor(starter=lambda: channel)
    supervisor.require()
    supervisor._lock.acquire()  # 模拟守护线程冻结在锁上
    try:
        worker = threading.Thread(target=_stop_all_atexit, daemon=True)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()  # 限时跳过,不死锁
        assert channel.stop_calls == 0
    finally:
        supervisor._lock.release()
        supervisor.shutdown()
