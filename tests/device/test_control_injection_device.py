"""直接控制真机 e2e(工单 04/05):tap 导航、swipe 滚动、按键注入、手势 WS。

注入坐标由原生定位结果换算为归一化值,断言以业务状态为准
(页面变化),不依赖画面像素。单操作者凭据制:注入须携带
``X-Viewer-Control``,手势 WS 在首条消息里认证。
"""

import time

import pytest
from fastapi.testclient import TestClient

from mobile_auto import DeviceSession, Locator, Target
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore

pytestmark = pytest.mark.device


ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def control_http(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    session.release_lease()
    manager = _ChannelManager(session)
    app = create_app(session, SnapshotStore(), channel_manager=manager)
    client = TestClient(app)
    res = client.post("/api/v1/control", json={"enabled": True})
    assert res.status_code == 200
    token = res.json()["token"]
    assert token
    yield client, session, token
    client.post("/api/v1/control", json={"enabled": False}, headers=_head(token))
    manager.shutdown()
    session.close()


def _head(token):
    return {"x-viewer-control": token}


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
    client, session, token = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(row) is True
    (element,) = await adapter.find_elements(row)
    res = client.post(
        "/api/v1/input/touch", json=_norm_center(element.bounds), headers=_head(token)
    )
    assert res.status_code == 200
    # 子页打开:子页特有元素出现即成功(页面过渡轮询,不定长等待)
    assert await _wait_true(
        lambda: adapter.is_visible(Target(Locator.TEXT, "网络加速"))
    )


@pytest.mark.device
async def test_back_key_returns_from_subpage(control_http):
    client, session, token = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(row) is True
    (element,) = await adapter.find_elements(row)
    res = client.post(
        "/api/v1/input/touch", json=_norm_center(element.bounds), headers=_head(token)
    )
    assert res.status_code == 200
    assert await _wait_true(lambda: adapter.is_visible(Target(Locator.TEXT, "网络加速")))
    _inject_gap()
    res = client.post("/api/v1/input/key", json={"name": "back"}, headers=_head(token))
    assert res.status_code == 200
    assert await _wait_true(lambda: adapter.is_visible(row))


async def _wait_true(probe, timeout: float = 5.0):
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await probe():
            return True
        await asyncio.sleep(0.2)
    return False


@pytest.mark.device
async def test_swipe_changes_screen(control_http):
    client, session, token = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    before = await adapter.screenshot()
    _inject_gap()
    res = client.post(
        "/api/v1/input/swipe",
        json={"nx0": 0.5, "ny0": 0.8, "nx1": 0.5, "ny1": 0.2, "duration_ms": 300},
        headers=_head(token),
    )
    assert res.status_code == 200
    time.sleep(1.0)
    after = await adapter.screenshot()
    assert before != after


async def test_gesture_ws_drag_scrolls_screen(control_http):
    """手势 WS 全链路:DOWN→MOVE×6→UP 逐条 ack,设备画面真实变化。"""
    client, session, token = control_http
    from mobile_auto import MobileAdapter

    adapter = MobileAdapter(session)
    await adapter.goto("")
    before = await adapter.screenshot()
    with client.websocket_connect("/api/v1/input/gesture", headers=ORIGIN) as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "start", "nx": 0.5, "ny": 0.8, "seq": 1})
        assert ws.receive_json() == {"kind": "ack", "phase": "down", "seq": 1}
        for seq, ny in enumerate((0.7, 0.6, 0.5, 0.4, 0.3, 0.2), start=2):
            ws.send_json({"type": "move", "nx": 0.5, "ny": ny, "seq": seq})
            assert ws.receive_json() == {"kind": "ack", "phase": "move", "seq": seq}
        ws.send_json({"type": "end", "seq": 8})
        assert ws.receive_json() == {"kind": "ack", "phase": "end", "seq": 8}
        assert ws.receive_json() == {"kind": "ended", "reason": "end"}
    time.sleep(1.0)
    after = await adapter.screenshot()
    assert before != after
    assert session.state.value == "ready"  # 手势结束后租约释放
