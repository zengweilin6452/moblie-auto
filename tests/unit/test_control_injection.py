"""直接控制端点单元测试(工单 04):门控、坐标换算、节流、租约拒绝。"""

import pytest
from conftest import NullLease, ViewerBackend
from fastapi.testclient import TestClient

from mobile_auto import DeviceSession, MobileConfig
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore


class FakeControlChannel:
    def __init__(self, serial):
        self.serial = serial
        self.started = False
        self.stopped = False
        self.taps = []
        self.swipes = []
        self.keys = []
        self.wakes = 0

    def start(self):
        self.started = True

    def healthy(self):
        return self.started and not self.stopped

    def screen_size(self):
        return 1080, 2400

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, x0, y0, x1, y1, duration_s=0.3):
        self.swipes.append((x0, y0, x1, y1, duration_s))

    def press_key(self, name):
        from mobile_auto.errors import InvalidTarget

        if name not in ("back", "home"):
            raise InvalidTarget(f"不支持的按键:{name!r};支持:back / home")
        self.keys.append(name)

    def wake(self):
        self.wakes += 1

    def stop(self):
        self.stopped = True


@pytest.fixture
def control_env(tmp_path):
    channel = FakeControlChannel("SERIAL1")
    config = MobileConfig(device_serial="SERIAL1", assets_dir=tmp_path / "assets")
    session = DeviceSession(
        config,
        backend_factory=lambda: ViewerBackend(),
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    session.release_lease()
    manager = _ChannelManager(
        session, retry_init=0.05, channel_factory=lambda serial: channel
    )
    app = create_app(
        session, SnapshotStore(), preview_interval=0.05, channel_manager=manager
    )
    yield TestClient(app), session, channel, manager
    manager.shutdown()
    session.close()


def test_control_disabled_rejects_injection(control_env):
    http, _, channel, _ = control_env
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
    assert res.status_code == 400
    assert res.json()["code"] == "control-disabled"
    assert channel.taps == []


def test_control_toggle_and_tap_converts_normalized_pixels(control_env):
    http, _, channel, _ = control_env
    assert http.post("/api/v1/control", json={"enabled": True}).json()["enabled"] is True
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
    assert res.status_code == 200
    assert channel.taps == [(540, 1200)]  # 1080x2400 的中心
    assert http.post("/api/v1/control", json={"enabled": False}).json()["enabled"] is False
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
    assert res.status_code == 400


def test_tap_normalization_clamped(control_env):
    http, _, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    assert http.post("/api/v1/input/touch", json={"nx": 1.5, "ny": -0.2}).status_code == 200
    assert channel.taps == [(1080, 0)]  # 夹限到屏幕范围


def test_tap_rejects_non_numeric(control_env):
    http, _, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    res = http.post("/api/v1/input/touch", json={"nx": "0.5", "ny": 0.5})
    assert res.status_code == 422
    assert channel.taps == []


def test_swipe_endpoint_converts_and_clamps_duration(control_env):
    http, _, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    res = http.post(
        "/api/v1/input/swipe",
        json={"nx0": 0.5, "ny0": 0.8, "nx1": 0.5, "ny1": 0.2, "duration_ms": 250},
    )
    assert res.status_code == 200
    assert channel.swipes == [(540, 1920, 540, 480, 0.25)]


def test_key_endpoint_validates_names(control_env):
    import time

    http, _, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    assert http.post("/api/v1/input/key", json={"name": "back"}).status_code == 200
    assert channel.keys == ["back"]
    time.sleep(0.06)  # 越过节流窗口
    res = http.post("/api/v1/input/key", json={"name": "reboot"})
    assert res.status_code == 422
    assert res.json()["code"] == "InvalidTarget"


def test_wake_endpoint(control_env):
    http, _, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    assert http.post("/api/v1/input/wake").status_code == 200
    assert channel.wakes == 1


def test_injection_throttled_within_50ms(control_env):
    http, _, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    assert http.post("/api/v1/input/touch", json={"nx": 0.1, "ny": 0.1}).status_code == 200
    res = http.post("/api/v1/input/touch", json={"nx": 0.2, "ny": 0.2})
    assert res.status_code == 429
    assert res.json()["code"] == "throttled"


def test_injection_rejected_when_lease_held(control_env):
    import time

    http, session, channel, _ = control_env
    http.post("/api/v1/control", json={"enabled": True})
    session.acquire_lease()  # 模拟 pytest 持有租约(LEASED 状态)
    try:
        res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
        assert res.status_code in (400, 409)  # 节流或占用,二者其一先命中
        time.sleep(0.06)  # 越过节流窗口,确认占用报错
        res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
        assert res.status_code == 409
        assert res.json()["code"] == "DeviceBusy"
    finally:
        session.release_lease()
    assert channel.taps == []  # 未发生注入


def test_injection_fails_when_channel_unavailable(control_env):
    http, session, channel, manager = control_env
    http.post("/api/v1/control", json={"enabled": True})
    channel.stopped = True  # 通道不健康
    import time

    time.sleep(0.06)
    res = http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5})
    assert res.status_code == 503
    assert res.json()["code"] == "StreamUnavailable"


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
    http, session, channel, _ = control_env
    journal, lines = _viewer_journal(tmp_path, session)
    http.post("/api/v1/control", json={"enabled": True})
    assert http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}).status_code == 200
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
    http, session, channel, _ = control_env
    channel.channel_name = "upstream"
    journal, lines = _viewer_journal(tmp_path, session)
    http.post("/api/v1/control", json={"enabled": True})
    assert http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}).status_code == 200
    [line] = lines()
    assert line["channel"] == "upstream"  # 实际通道,不是写死的 scrcpy
    journal.close()


def test_viewer_injection_failure_is_recorded(control_env, tmp_path):
    http, session, channel, _ = control_env
    journal, lines = _viewer_journal(tmp_path, session)
    http.post("/api/v1/control", json={"enabled": True})
    channel.stopped = True
    assert http.post("/api/v1/input/touch", json={"nx": 0.1, "ny": 0.1}).status_code == 503
    [line] = lines()
    assert line["op"] == "viewer.tap"
    assert line["outcome"] == "error"
    assert line["error_type"] == "StreamUnavailable"
    journal.close()
