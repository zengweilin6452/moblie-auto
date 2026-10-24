"""真机拾取→验证端到端(工单 10):拾取候选的表达式粘贴进用例直接可用。

TestClient 挂真机会话:采集含树快照 → 按 WLAN 文本坐标拾取 →
验证候选 → 表达式构造 Target 经适配器查询通过。
"""

import pytest
from fastapi.testclient import TestClient

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target
from mobile_auto.viewer.app import create_app
from mobile_auto.viewer.store import SnapshotStore


@pytest.fixture
def viewer(mobile_config):
    import uiautomator2 as u2

    device = u2.connect(mobile_config.device_serial)
    if not device.info.get("screenOn"):  # 息屏时 u2 dump 会挂起
        device.screen_on()
        device.swipe(0.5, 0.9, 0.5, 0.2, duration=0.2)
    session = DeviceSession(mobile_config)
    session.start()
    session.release_lease()
    app = create_app(session, SnapshotStore())
    yield TestClient(app), MobileAdapter(session)
    session.close()


@pytest.mark.device
def test_pick_validate_expression_roundtrip_on_device(viewer):
    http, adapter = viewer
    snap = http.post("/api/v1/snapshots", json={"with_tree": True}).json()
    assert snap["tree_error"] is None

    # 在树中找 WLAN 文本节点,取其中心作为拾取点
    tree = http.get(f"/api/v1/snapshots/{snap['snapshot_id']}/tree").json()["nodes"]
    wlan = next(n for n in tree if n["text"] == "WLAN")
    bx = wlan["bounds"]
    point = ((bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2)

    pick = http.post(
        "/api/v1/pick",
        json={"snapshot_id": snap["snapshot_id"], "x": point[0], "y": point[1]},
    ).json()
    assert pick["candidates"], "拾取应有候选"
    text_candidate = next(
        loc
        for node in pick["candidates"]
        for loc in node["locators"]
        if loc["by"] == "text" and loc["value"] == "WLAN"
    )

    # 验证:重新采集查询,唯一匹配通过
    result = http.post(
        "/api/v1/locators/validate", json={"target": text_candidate["target"]}
    ).json()
    assert result["count"] >= 1

    # 表达式 → Target → 驱动查询:粘贴进用例直接通过
    target = Target(Locator(text_candidate["target"]["by"]), text_candidate["target"]["value"])
    handles = await_adapter_find(adapter, target)
    assert handles, "表达式构造的 Target 经适配器查询应有结果"


def await_adapter_find(adapter, target):
    import asyncio

    return asyncio.run(adapter.find_elements(target))
