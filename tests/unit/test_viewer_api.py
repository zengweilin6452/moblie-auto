"""网页 HTTP/WS API 外部行为:注入伪造后端会话,TestClient 驱动。"""

import pytest
from conftest import ViewerBackend, make_viewer_session

from mobile_auto.viewer.app import capture_snapshot_sync


def test_status_reports_device_and_lease(client):
    http, session, _ = client
    res = http.get("/api/v1/status")
    assert res.status_code == 200
    body = res.json()
    assert body["device"]["serial"] == "SERIAL1"
    assert body["device"]["state"] == "ready"
    assert body["capture"]["can_capture"] is True
    session.acquire_lease()
    body = http.get("/api/v1/status").json()
    assert body["device"]["busy"] is True  # pytest 持有租约时如实显示
    assert body["capture"]["can_capture"] is False
    session.release_lease()


def test_snapshot_binds_image_and_tree_with_timestamps(client):
    http, _, backend = client
    res = http.post("/api/v1/snapshots", json={"with_tree": True})
    assert res.status_code == 200
    body = res.json()
    assert body["width"] == 150 and body["height"] == 200
    assert body["orientation"] == "portrait"
    assert body["captured_at"] and body["tree_captured_at"]
    assert body["consistent"] is True

    image = http.get(f"/api/v1/snapshots/{body['snapshot_id']}/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    tree = http.get(f"/api/v1/snapshots/{body['snapshot_id']}/tree")
    assert tree.status_code == 200
    nodes = tree.json()["nodes"]
    assert any(n["text"] == "WLAN" for n in nodes)
    assert any(n["children"] for n in nodes)  # 父子关系保留


def test_busy_session_rejects_capture_with_409(client):
    http, session, _ = client
    session.acquire_lease()  # pytest 持有租约
    res = http.post("/api/v1/snapshots", json={"with_tree": False})
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "DeviceBusy"
    assert body["message"] and body["request_id"]
    session.release_lease()


def test_unknown_snapshot_404_envelope(client):
    http, _, _ = client
    res = http.get("/api/v1/snapshots/doesnotexist/image")
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "not-found" and body["request_id"]


def test_freeze_pins_snapshot(client):
    http, _, _ = client
    sid = http.post("/api/v1/snapshots", json={}).json()["snapshot_id"]
    assert http.post(f"/api/v1/snapshots/{sid}/freeze", json={"frozen": True}).status_code == 200
    assert http.get(f"/api/v1/snapshots/{sid}/image").status_code == 200


def test_cross_origin_and_host_rejected(client):
    http, _, _ = client
    res = http.get("/api/v1/status", headers={"origin": "http://evil.example.com"})
    assert res.status_code == 403
    res = http.get("/api/v1/status", headers={"host": "evil.example.com:8765"})
    assert res.status_code == 403


def test_static_index_served(client):
    res = client[0].get("/")
    assert res.status_code == 200
    assert "mobile-auto" in res.text


def test_preview_ws_pushes_frames(client):
    http, session, _ = client
    with http.websocket_connect("/api/v1/preview") as ws:
        frame = ws.receive_json()
    assert frame["kind"] == "frame"
    assert frame["captured_at"] and frame["jpeg_base64"]


def test_preview_ws_busy_status_when_lease_held(client):
    http, session, _ = client
    session.acquire_lease()  # pytest 持有租约:不采集,推送占用状态
    with http.websocket_connect("/api/v1/preview") as ws:
        frame = ws.receive_json()
    session.release_lease()
    assert frame["kind"] == "status"
    assert frame["busy"] is True


def test_capture_sync_returns_snapshot():
    session, _ = make_viewer_session(ViewerBackend())
    snapshot = capture_snapshot_sync(session, with_tree=True)
    assert snapshot.png.startswith(b"\x89PNG")
    assert snapshot.tree_json is not None
    assert "WLAN" in snapshot.tree_json


def test_fifth_ws_client_rejected_per_device(client):
    """WS 每设备上限 4:第 5 个连接被拒。"""
    http, session, _ = client
    from contextlib import ExitStack

    from starlette.websockets import WebSocketDisconnect

    with ExitStack() as stack:
        for _ in range(4):
            ws = stack.enter_context(http.websocket_connect("/api/v1/preview"))
            ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with http.websocket_connect("/api/v1/preview"):
                pass  # 超出单设备上限:拒绝


def test_devices_endpoint_lists_default_device(client):
    http, _, _ = client
    res = http.get("/api/v1/devices")
    assert res.status_code == 200
    devices = res.json()["devices"]
    assert devices == [
        {"serial": "SERIAL1", "state": "device", "session": "ready", "default": True}
    ]


def test_device_param_routes_to_that_devices_session(tmp_path):
    """?device= 寻址注册表内对应会话;设备间会话与采集互不相干。"""
    from fastapi.testclient import TestClient
    from fakes import NullLease
    from mobile_auto import MobileConfig
    from mobile_auto.core.session import DeviceSession
    from mobile_auto.viewer.app import _DeviceRegistry, create_app
    from mobile_auto.viewer.store import SnapshotStore

    def make(serial):
        config = MobileConfig(device_serial=serial, assets_dir=tmp_path / "assets")
        session = DeviceSession(
            config,
            backend_factory=lambda: ViewerBackend(),
            device_enumerator=lambda: {"SERIAL1": "device", "SERIAL2": "device"},
            lease_factory=lambda s: NullLease(s),
        )
        session.start()
        session.release_lease()
        return session

    registry = _DeviceRegistry(
        MobileConfig(device_serial="SERIAL1", assets_dir=tmp_path / "assets"),
        enumerator=lambda: {"SERIAL1": "device", "SERIAL2": "device"},
        session_factory=make,
    )
    app = create_app(registry=registry)
    http = TestClient(app)
    try:
        body1 = http.post("/api/v1/snapshots?device=SERIAL1", json={}).json()
        body2 = http.post("/api/v1/snapshots?device=SERIAL2", json={}).json()
        assert body1["snapshot_id"] and body2["snapshot_id"]
        devices = http.get("/api/v1/devices").json()["devices"]
        assert {d["serial"] for d in devices} == {"SERIAL1", "SERIAL2"}
        # 默认设备(config.device_serial)无须参数
        body3 = http.post("/api/v1/snapshots", json={}).json()
        assert body3["snapshot_id"]
    finally:
        registry.shutdown()
