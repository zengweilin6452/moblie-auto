"""连续手势 WS 单元测试(web-preview 05):序列、租约、并发与清理。"""

import time

import pytest
from conftest import acquire
from starlette.websockets import WebSocketDisconnect

# control_env 夹具由 conftest.py 提供(与控制注入测试共用装配)

ORIGIN = {"origin": "http://testserver"}
WAIT = 5.0


def _wait_until(predicate, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_gesture_requires_strict_origin(control_env):
    http, _, _, _, _ = control_env
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with http.websocket_connect("/api/v1/input/gesture"):
            pass
    assert exc_info.value.code == 4403


def test_gesture_requires_valid_credential(control_env):
    http, _, _, _, _ = control_env
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": "nope"})
        error = ws.receive_json()
        assert error["code"] == "forbidden"


def test_gesture_down_move_up_sequence(control_env):
    http, session, channel, _, _ = control_env
    token = acquire(http)
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.1, "ny": 0.1, "seq": 1})
        ws.send_json({"type": "move", "nx": 0.5, "ny": 0.5, "seq": 2})
        ws.send_json({"type": "end", "seq": 3})
        assert ws.receive_json() == {"kind": "ack", "phase": "down", "seq": 1}
        assert ws.receive_json() == {"kind": "ack", "phase": "move", "seq": 2}
        assert ws.receive_json() == {"kind": "ack", "phase": "end", "seq": 3}
        ended = ws.receive_json()
        assert ended == {"kind": "ended", "reason": "end"}
    # 像素换算(1080x2400):DOWN 保鲜基准,UP 沿用最后一点
    assert channel.touches == [
        ("down", 108, 240),
        ("move", 540, 1200),
        ("up", 540, 1200),
    ]
    assert session.state.value == "ready"  # 租约在 end 后释放


def test_gesture_seq_monotonic_enforced(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.1, "ny": 0.1, "seq": 1})
        assert ws.receive_json()["kind"] == "ack"
        ws.send_json({"type": "move", "nx": 0.2, "ny": 0.2, "seq": 1})  # 不递增
        error = ws.receive_json()
        assert error["code"] == "bad-seq"
    # 服务端清理路径:尽力 UP 并释放
    assert _wait_until(lambda: any(t[0] == "up" for t in channel.touches))


def test_gesture_second_connection_rejected_while_busy(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as first:
        first.send_json({"token": token})
        first.send_json({"type": "start", "nx": 0.1, "ny": 0.1, "seq": 1})
        assert first.receive_json()["kind"] == "ack"
        with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as second:
            second.send_json({"token": token})
            assert second.receive_json()["code"] == "gesture-busy"
        # 首个手势继续可用
        first.send_json({"type": "move", "nx": 0.3, "ny": 0.3, "seq": 2})
        assert first.receive_json()["kind"] == "ack"
        first.send_json({"type": "end", "seq": 3})
        assert first.receive_json()["kind"] == "ack"
    assert _wait_until(lambda: channel.touches[-1][0] == "up")


def test_gesture_disconnect_cleanup_sends_up_and_releases(control_env):
    http, session, channel, _, _ = control_env
    token = acquire(http)
    ws = http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN).__enter__()
    try:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.5, "ny": 0.5, "seq": 1})
        assert ws.receive_json()["kind"] == "ack"
    finally:
        ws.close()  # 断连:服务端尽力 UP 并释放租约
    assert _wait_until(lambda: any(t[0] == "up" for t in channel.touches))
    assert _wait_until(lambda: session.state.value == "ready")


def test_gesture_start_rejected_while_preview_shows_u2(control_env):
    """u2 回退画面只读:DOWN 之前就拒绝,不取租约、不注入。"""
    http, session, channel, manager, _ = control_env
    token = acquire(http)
    manager.note_preview_source("u2-fallback")
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.1, "ny": 0.1, "seq": 1})
        error = ws.receive_json()
        assert error["kind"] == "error"
        assert error["code"] == "StreamUnavailable"
    assert channel.touches == []
    assert _wait_until(lambda: session.state.value == "ready")  # DOWN 未确立即释放


def test_gesture_move_stops_when_preview_falls_back_to_u2(control_env):
    """手势中途进入 u2 回退:停止发送 MOVE,尽力 UP 并按结局语义清理。"""
    http, session, channel, manager, _ = control_env
    token = acquire(http)
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.5, "ny": 0.8, "seq": 1})
        assert ws.receive_json() == {"kind": "ack", "phase": "down", "seq": 1}
        manager.note_preview_source("u2-fallback")
        ws.send_json({"type": "move", "nx": 0.5, "ny": 0.6, "seq": 2})
        error = ws.receive_json()
        assert error["kind"] == "error"
        assert error["code"] == "StreamUnavailable"
        assert ws.receive_json() == {"kind": "ended", "reason": "error"}
    assert [touch[0] for touch in channel.touches] == ["down", "up"]  # 未发 MOVE
    assert _wait_until(lambda: session.state.value == "ready")


def test_gesture_idle_timeout_best_effort_up(control_env, monkeypatch):
    import mobile_auto.viewer.app as viewer_app

    monkeypatch.setattr(viewer_app, "_GESTURE_IDLE_TIMEOUT_SECONDS", 0.2)
    http, session, channel, _, _ = control_env
    token = acquire(http)
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.5, "ny": 0.5, "seq": 1})
        assert ws.receive_json()["kind"] == "ack"
        ended = ws.receive_json()
        assert ended == {"kind": "ended", "reason": "idle-timeout"}
    assert _wait_until(lambda: any(t[0] == "up" for t in channel.touches))
    assert _wait_until(lambda: session.state.value == "ready")
    # 超时释放后,同设备可再次领用手势
    with http.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.2, "ny": 0.2, "seq": 2})
        assert ws.receive_json()["kind"] == "ack"
        ws.send_json({"type": "cancel", "seq": 3})
        assert ws.receive_json()["kind"] == "ack"
