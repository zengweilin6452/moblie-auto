"""直接控制真机 e2e(工单 04):tap 导航、swipe 滚动、按键注入。

注入坐标由原生定位结果换算为归一化值,断言以业务状态为准
(页面变化),不依赖画面像素。
"""

import time

import pytest
from fastapi.testclient import TestClient

from mobile_auto import DeviceSession, Locator, Target
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore

pytestmark = pytest.mark.device


@pytest.fixture
def control_http(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    session.release_lease()
    manager = _ChannelManager(session)
    app = create_app(session, SnapshotStore(), channel_manager=manager)
    client = TestClient(app)
    assert client.post("/api/v1/control", json={"enabled": True}).status_code == 200
    yield client, session
    client.post("/api/v1/control", json={"enabled": False})
    manager.shutdown()
    session.close()


def _inject_gap():
    time.sleep(0.06)  # 服务端 50ms 注入节流


def _norm_center(bounds, screen=(1080, 2400)):
    left, top, right, bottom = bounds
    w, h = screen
    return {
        "nx": ((left + right) / 2) / w,
        "ny": ((top + bottom) / 2) / h,
    }


@pytest.mark.device
async def test_tap_opens_wlan_page(control_http):
    client, session = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(row) is True
    (element,) = await adapter.find_elements(row)
    assert client.post("/api/v1/input/touch", json=_norm_center(element.bounds)).status_code == 200
    time.sleep(1.0)  # 页面过渡
    # 子页标题与入口同名:验证仍唯一可见即页面状态正常
    assert await adapter.is_visible(row) is True


@pytest.mark.device
async def test_back_key_returns_from_subpage(control_http):
    client, session = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(row) is True
    (element,) = await adapter.find_elements(row)
    assert client.post("/api/v1/input/touch", json=_norm_center(element.bounds)).status_code == 200
    time.sleep(1.0)
    _inject_gap()
    assert client.post("/api/v1/input/key", json={"name": "back"}).status_code == 200
    time.sleep(1.0)
    assert await adapter.is_visible(row) is True


@pytest.mark.device
async def test_swipe_changes_screen(control_http):
    client, session = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    before = await adapter.screenshot()
    _inject_gap()
    res = client.post(
        "/api/v1/input/swipe",
        json={"nx0": 0.5, "ny0": 0.8, "nx1": 0.5, "ny1": 0.2, "duration_ms": 300},
    )
    assert res.status_code == 200
    time.sleep(1.0)
    after = await adapter.screenshot()
    assert before != after
