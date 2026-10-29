"""直接控制端点单元测试(web-preview 04):凭据门控、坐标换算、节流、租约拒绝。

FakeControlChannel / control_env / acquire / head 定义在 conftest.py,
与手势 WS 测试共用装配。
"""

import time

import pytest  # noqa: F401  (control_env 夹具经 conftest 自动注册)
from conftest import acquire, head

from mobile_auto.viewer.app import create_app  # noqa: F401  (装配在 conftest)
from mobile_auto.viewer.store import SnapshotStore  # noqa: F401


def test_injection_without_credential_rejected(control_env):
    http, _, channel, _, _ = control_env
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
    assert res.status_code == 400
    assert res.json()["code"] == "control-credential-required"
    assert channel.taps == []


def test_control_ownership_and_tap_converts_normalized_pixels(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token))
    assert res.status_code == 200
    assert channel.taps == [(540, 1200)]  # 1080x2400 的中心
    # 释放后无凭据持有者:再注入被拒
    assert http.post(
        "/api/v1/control", json={"enabled": False}, headers=head(token)
    ).json()["ok"] is True
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token))
    assert res.status_code == 400
    assert res.json()["code"] == "control-disabled"


def test_control_ownership_idempotent_and_conflict(control_env):
    http, _, _, _, _ = control_env
    token = acquire(http)
    # 持证重复启用:幂等续期,返回同一凭据
    again = http.post("/api/v1/control", json={"enabled": True}, headers=head(token))
    assert again.status_code == 200
    assert again.json()["token"] == token
    # 他人(无凭据/凭据不符)领用:409 不抢占
    other = http.post("/api/v1/control", json={"enabled": True})
    assert other.status_code == 409
    assert other.json()["code"] == "control-busy"
    wrong = http.post(
        "/api/v1/control", json={"enabled": True}, headers=head("not-the-token")
    )
    assert wrong.status_code == 409
    # 释放需持证:凭据不符 403
    assert (
        http.post("/api/v1/control", json={"enabled": False}, headers=head("bad")).status_code
        == 403
    )


def test_injection_with_wrong_credential_rejected(control_env):
    http, _, channel, _, _ = control_env
    acquire(http)
    res = http.post(
        "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head("wrong-token")
    )
    assert res.status_code == 403
    assert channel.taps == []


def test_heartbeat_renews_and_expiry_revokes(control_env):
    http, _, channel, _, app = control_env
    token = acquire(http)
    ok = http.post("/api/v1/control/heartbeat", headers=head(token))
    assert ok.status_code == 200
    assert ok.json()["ttl_seconds"] > 0
    # 心跳凭据不符:403
    assert (
        http.post("/api/v1/control/heartbeat", headers=head("bad")).status_code == 403
    )
    # 失联超 TTL:懒撤权(直接把 seen 拨回 6 秒前,不等真实 5 秒)
    serial = "SERIAL1"
    with app.state.control_lock:
        app.state.control_seen[serial] -= 6.0
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token))
    assert res.status_code == 400
    assert res.json()["code"] == "control-disabled"
    assert channel.taps == []
    # 撤权后心跳也失效
    assert (
        http.post("/api/v1/control/heartbeat", headers=head(token)).status_code == 403
    )


def test_tap_normalization_clamped(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    res = http.post(
        "/api/v1/input/touch", json={"nx": 1.5, "ny": -0.2}, headers=head(token)
    )
    assert res.status_code == 200
    assert channel.taps == [(1080, 0)]  # 夹限到屏幕范围


def test_tap_rejects_non_numeric(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    res = http.post(
        "/api/v1/input/touch", json={"nx": "0.5", "ny": 0.5}, headers=head(token)
    )
    assert res.status_code == 422
    assert channel.taps == []


def test_swipe_endpoint_converts_and_clamps_duration(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    res = http.post(
        "/api/v1/input/swipe",
        json={"nx0": 0.5, "ny0": 0.8, "nx1": 0.5, "ny1": 0.2, "duration_ms": 250},
        headers=head(token),
    )
    assert res.status_code == 200
    assert channel.swipes == [(540, 1920, 540, 480, 0.25)]


def test_key_endpoint_validates_names(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    assert (
        http.post("/api/v1/input/key", json={"name": "back"}, headers=head(token)).status_code
        == 200
    )
    assert channel.keys == ["back"]
    time.sleep(0.06)  # 越过节流窗口
    res = http.post("/api/v1/input/key", json={"name": "reboot"}, headers=head(token))
    assert res.status_code == 422
    assert res.json()["code"] == "InvalidTarget"


def test_wake_endpoint(control_env):
    http, _, channel, _, _ = control_env
    token = acquire(http)
    assert http.post("/api/v1/input/wake", headers=head(token)).status_code == 200
    assert channel.wakes == 1


def test_injection_throttled_within_50ms(control_env):
    http, _, _, _, _ = control_env
    token = acquire(http)
    assert (
        http.post(
            "/api/v1/input/touch", json={"nx": 0.1, "ny": 0.1}, headers=head(token)
        ).status_code
        == 200
    )
    res = http.post(
        "/api/v1/input/touch", json={"nx": 0.2, "ny": 0.2}, headers=head(token)
    )
    assert res.status_code == 429
    assert res.json()["code"] == "throttled"


def test_injection_rejected_when_lease_held(control_env):
    http, session, channel, _, _ = control_env
    token = acquire(http)
    session.acquire_lease()  # 模拟 pytest 持有租约(LEASED 状态)
    try:
        time.sleep(0.06)
        res = http.post(
            "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token)
        )
        assert res.status_code == 409
        assert res.json()["code"] == "DeviceBusy"
    finally:
        session.release_lease()
    assert channel.taps == []  # 未发生注入


def test_injection_fails_when_channel_unavailable(control_env):
    http, session, channel, _, _ = control_env
    token = acquire(http)
    channel.stopped = True  # 通道不健康
    time.sleep(0.06)
    res = http.post(
        "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token)
    )
    assert res.status_code == 503
    assert res.json()["code"] == "StreamUnavailable"


def test_injection_rejected_while_preview_shows_u2(control_env):
    """预览回退 u2 时画面只读:不得按 u2 截图坐标注入。"""
    http, session, channel, manager, _ = control_env
    token = acquire(http)
    manager.note_preview_source("u2-fallback")
    for path, body in (
        ("/api/v1/input/touch", {"nx": 0.5, "ny": 0.5}),
        ("/api/v1/input/swipe", {"nx0": 0.1, "ny0": 0.1, "nx1": 0.9, "ny1": 0.9}),
        ("/api/v1/input/key", {"name": "back"}),
    ):
        time.sleep(0.06)  # 离散请求 50ms 节流
        res = http.post(path, json=body, headers=head(token))
        assert res.status_code == 503, path
        assert res.json()["code"] == "StreamUnavailable", path
    assert channel.taps == [] and channel.swipes == [] and channel.keys == []


def test_u2_holdoff_expires(control_env, monkeypatch):
    """scrcpy 画面恢复(超出 3 秒窗口)后控制自动恢复,无需人工重置。"""
    import mobile_auto.viewer.app as viewer_app

    monkeypatch.setattr(viewer_app, "_U2_CONTROL_HOLDOFF_SECONDS", 0.2)
    http, session, channel, manager, _ = control_env
    token = acquire(http)
    manager.note_preview_source("u2-fallback")
    time.sleep(0.3)
    res = http.post(
        "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token)
    )
    assert res.status_code == 200
    assert channel.taps == [(540, 1200)]


def test_wake_allowed_while_preview_shows_u2(control_env):
    """亮屏不依赖画面坐标,u2 回退期间仍是可用的恢复动作。"""
    http, session, channel, manager, _ = control_env
    token = acquire(http)
    manager.note_preview_source("u2-fallback")
    time.sleep(0.06)
    res = http.post("/api/v1/input/wake", json={}, headers=head(token))
    assert res.status_code == 200
    assert channel.wakes == 1


def test_static_screen_stays_controllable(control_env):
    """静屏不得误判滞后:该编码器静屏完全不出包(实测滚动 13s/183 帧、
    静置 0 帧),按包到达计时会让操作者无法再点按解冻。"""
    http, session, channel, _, _ = control_env
    token = acquire(http)
    time.sleep(3.1)  # 超过规格的 3 秒包到达窗口,期间没有任何编码包
    res = http.post(
        "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token)
    )
    assert res.status_code == 200
    assert channel.taps == [(540, 1200)]


def _viewer_journal(tmp_path, session):
    import json

    from mobile_auto.evidence.journal import Journal

    journal = Journal(tmp_path / "viewer" / "SERIAL1", session.session_id)
    session.set_journal(journal)

    def lines():
        return [
            json.loads(line)
            for line in journal.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return journal, lines


def test_viewer_injection_writes_action_journal(control_env, tmp_path):
    http, session, channel, _, _ = control_env
    journal, lines = _viewer_journal(tmp_path, session)
    token = acquire(http)
    assert (
        http.post(
            "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token)
        ).status_code
        == 200
    )
    [line] = lines()
    assert line["op"] == "viewer.tap"
    assert line["outcome"] == "ok"
    # 伪造通道未声明实际通道名:不臆造 channel,更不写死 scrcpy
    assert "channel" not in line
    assert line["point"] == [0.5, 0.5]
    assert line["normalized"] is True
    assert line["session"] == session.session_id
    journal.close()


def test_viewer_journal_records_real_upstream_channel(control_env, tmp_path):
    http, session, channel, _, _ = control_env
    channel.channel_name = "upstream"
    journal, lines = _viewer_journal(tmp_path, session)
    token = acquire(http)
    assert (
        http.post(
            "/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}, headers=head(token)
        ).status_code
        == 200
    )
    [line] = lines()
    assert line["channel"] == "upstream"  # 实际通道,不是写死的 scrcpy
    journal.close()


def test_viewer_injection_failure_is_recorded(control_env, tmp_path):
    http, session, channel, _, _ = control_env
    journal, lines = _viewer_journal(tmp_path, session)
    token = acquire(http)
    channel.stopped = True
    assert (
        http.post(
            "/api/v1/input/touch", json={"nx": 0.1, "ny": 0.1}, headers=head(token)
        ).status_code
        == 503
    )
    [line] = lines()
    assert line["op"] == "viewer.tap"
    assert line["outcome"] == "error"
    assert line["error_type"] == "StreamUnavailable"
    journal.close()
