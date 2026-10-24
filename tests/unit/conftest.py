"""查看器测试共用夹具:注入伪造后端的会话 + TestClient。"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from mobile_auto.viewer.app import create_app
from mobile_auto.viewer.store import SnapshotStore


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
        return (150, 200)

    def find_elements(self, by, value):
        from fakes import FakeElement

        title = FakeElement(
            (10, 20, 80, 40),
            text="WLAN",
            info={"className": "android.widget.TextView",
                  "resourceName": "android:id/title",
                  "contentDescription": "无线网络"},
        )
        if by.value == "resource_id":
            return [title] if "android:id/title" == value else []
        if by.value == "text":
            return [title] if value == "WLAN" else []
        if by.value == "xpath" and value == "//node":
            return [FakeElement((0, 0, 10, 10)), FakeElement((20, 20, 30, 30))]
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


def real_png(seed=3, w=150, h=200) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 255, size=(h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", arr[:, :, ::-1])
    assert ok
    return buf.tobytes()


TREE_XML = """<?xml version='1.0'?><hierarchy>
<node class="android.widget.FrameLayout" text="" resource-id="" bounds="[0,0][150,200]">
  <node class="android.widget.LinearLayout" text="" resource-id="" bounds="[0,0][150,200]">
    <node class="android.widget.TextView" text="WLAN" resource-id="android:id/title"
          content-desc="无线网络" bounds="[10,20][80,40]" clickable="true"/>
  </node>
</node></hierarchy>"""


def make_viewer_session(backend):
    from fakes import NullLease, make_session

    session, backend_ref = make_session(
        backend=backend, lease_factory=lambda s: NullLease(s)
    )
    session.start()
    session.release_lease()  # 网页空闲态:READY
    return session, backend_ref


@pytest.fixture
def client():
    session, backend = make_viewer_session(ViewerBackend())
    app = create_app(session, SnapshotStore(), preview_interval=0.1)
    return TestClient(app), session, backend
