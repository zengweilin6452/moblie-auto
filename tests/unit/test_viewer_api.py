"""网页 HTTP/WS API 外部行为:注入伪造后端会话,TestClient 驱动。"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from mobile_auto.viewer.app import capture_snapshot_sync, create_app
from mobile_auto.viewer.store import SnapshotStore

SCREEN = (150, 200)


def real_png(seed=3, w=150, h=200) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 255, size=(h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", arr[:, :, ::-1])
    assert ok
    return buf.tobytes()


TREE_XML = """<?xml version='1.0'?><hierarchy>
<node class="android.widget.FrameLayout" text="" resource-id="" bounds="[0,0][150,200]">
<node class="android.widget.TextView" text="WLAN" resource-id="android:id/title"
 bounds="[10,20][80,40]" clickable="true"/></node></hierarchy>"""


class ViewerBackend:
    """查看器测试后端:真实 PNG 截图 + 固定 UI 树。"""

    def __init__(self):
        self.png = real_png()
        self.capture_calls = 0

    def connect(self, serial):
        pass

    def close(self):
        pass

    def screen_size(self):
        return SCREEN

    def find_elements(self, by, value):
        return []

    def screenshot_png(self):
        self.capture_calls += 1
        return self.png

    def tap(self, x, y):
        pass

    def app_start(self, package, activity, stop=False):
        pass

    def open_deeplink(self, uri):
        pass

    def press_key(self, name):
        pass

    def swipe(self, sx, sy, ex, ey, duration):
        pass

    def dump_ui(self):
        return TREE_XML


@pytest.fixture
def client():
    backend = ViewerBackend()
    session, backend_ref = _make_session(backend)
    app = create_app(session, SnapshotStore(), preview_interval=0.1)
    return TestClient(app), session, backend_ref


def _make_session(backend):
    from fakes import NullLease, make_session

    session, backend_ref = make_session(backend=backend, lease_factory=lambda s: NullLease(s))
    session.start()
    session.release_lease()  # 网页空闲态:READY
    return session, backend_ref


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


def test_cross_origin_and_host_rejected():
    backend = ViewerBackend()
    session, _ = _make_session(backend)
    http = TestClient(create_app(session, SnapshotStore()))
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


def test_capture_sync_returns_snapshot(tmp_path=None):
    backend = ViewerBackend()
    session, _ = _make_session(backend)
    snapshot = capture_snapshot_sync(session, with_tree=True)
    assert snapshot.png.startswith(b"\x89PNG")
    assert snapshot.tree_json is not None
    assert "WLAN" in snapshot.tree_json


def test_third_ws_client_rejected(client):
    http, session, _ = client
    with http.websocket_connect("/api/v1/preview") as first:
        first.receive_json()
        with http.websocket_connect("/api/v1/preview") as second:
            second.receive_json()
            from starlette.websockets import WebSocketDisconnect

            with pytest.raises(WebSocketDisconnect):
                with http.websocket_connect("/api/v1/preview"):
                    pass  # 第三个客户端:超上限拒绝
