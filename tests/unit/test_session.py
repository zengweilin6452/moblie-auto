"""设备会话的外部行为:设备选择错误、状态转换、串行队列与关闭。"""

import threading

import pytest
from fakes import FakeBackend, make_session

from mobile_auto import (
    DeviceSession,
    DeviceUnavailable,
    MobileConfig,
    SessionFaulted,
)
from mobile_auto.core.session import SessionState, resolve_serial


@pytest.mark.parametrize(
    ("states", "configured", "match"),
    [
        ({}, None, "未发现任何设备"),
        ({"A": "device", "B": "device"}, None, "多台已授权设备在线"),
        ({"A": "device"}, "MISSING", "未连接"),
        ({"A": "unauthorized"}, None, "均未授权"),
        ({"A": "unauthorized"}, "A", "未授权"),
        ({"A": "offline"}, None, "状态异常"),
        ({"A": "offline"}, "A", "状态异常"),
        ({"A": "offline", "B": "unauthorized"}, None, "均未授权"),
    ],
)
def test_resolve_serial_rejects_ambiguous_or_wrong_devices(states, configured, match):
    with pytest.raises(DeviceUnavailable, match=match):
        resolve_serial(states, configured)


def test_resolve_serial_picks_unique_and_honors_configured():
    assert resolve_serial({"A": "device"}, None) == "A"
    assert resolve_serial({"A": "device", "B": "unauthorized"}, "A") == "A"


def test_session_start_connects_resolved_serial():
    session, backend = make_session(
        config=MobileConfig(),  # 未配置序列号:唯一已授权设备应被解析
        states={"S9": "device", "S1": "unauthorized"},
    )
    session.start()
    assert session.state is SessionState.LEASED
    assert session.serial == "S9"
    assert backend.connect_calls == ["S9"]


def test_failed_start_does_not_touch_backend_or_reach_ready():
    factory_calls = []
    session = DeviceSession(
        MobileConfig(),
        backend_factory=lambda: factory_calls.append(1) or FakeBackend(),
        device_enumerator=lambda: {},
    )
    with pytest.raises(DeviceUnavailable, match="未发现任何设备"):
        session.start()
    assert session.state is SessionState.NEW
    assert factory_calls == []


def test_double_start_rejected():
    session, _ = make_session()
    session.start()
    with pytest.raises(RuntimeError, match="已启动"):
        session.start()


def test_operation_before_start_rejected():
    session, _ = make_session()
    with pytest.raises(SessionFaulted, match="未就绪"):
        session.submit(lambda backend: 1)


def test_operations_execute_serially_in_submit_order():
    session, _ = make_session()
    session.start()
    order = []
    release = threading.Event()

    def blocking_op(backend):
        order.append("op1-start")
        assert release.wait(timeout=2)
        order.append("op1-end")

    def queued_op(backend):
        order.append("op2")

    future1 = session.submit(blocking_op)
    future2 = session.submit(queued_op)
    assert not future2.done()
    release.set()
    future1.result(timeout=2)
    future2.result(timeout=2)
    assert order == ["op1-start", "op1-end", "op2"]


def test_close_is_idempotent_and_closes_backend():
    session, backend = make_session()
    session.start()
    session.close()
    assert session.state is SessionState.CLOSED
    assert backend.closed
    session.close()  # 重复关闭安全


def test_close_without_start_never_touches_backend():
    session, backend = make_session()
    session.close()
    assert session.state is SessionState.CLOSED
    assert not backend.closed


def test_operation_after_close_rejected():
    session, _ = make_session()
    session.start()
    session.close()
    with pytest.raises(SessionFaulted, match="已关闭"):
        session.submit(lambda backend: 1)
