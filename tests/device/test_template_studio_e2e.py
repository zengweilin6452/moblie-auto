"""真机图像模板闭环(工单 11):框选制作 → 验证 → 复制表达式 → 驱动点击。

以服务端快照裁剪真实像素制作模板(与浏览器框选同一 API),
验证后把复制的 IMAGE 表达式内容直接作为 Target 执行点击,业务断言。
"""

import pytest
from fastapi.testclient import TestClient

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target
from mobile_auto.viewer.app import create_app
from mobile_auto.viewer.store import SnapshotStore


@pytest.fixture
def viewer(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    session.release_lease()
    app = create_app(session, SnapshotStore())
    yield TestClient(app), MobileAdapter(session), session.config
    session.close()


@pytest.mark.device
def test_template_studio_end_to_end_click(viewer):
    http, adapter, config = viewer

    # 0. 冷启动:清除搜索残留,保证主页 WLAN 行唯一
    import asyncio

    asyncio.run(adapter.goto(""))
    assert asyncio.run(adapter.wait_for(Target(Locator.TEXT, "WLAN"))) is True

    # 1. 采集快照(同帧制作与验证)
    snap = http.post("/api/v1/snapshots", json={"with_tree": False}).json()

    # 2. 框选:取主页 WLAN 行整行(含彩色图标),在树中唯一
    #    小米 ROM 偶发空 dump(docs/demo-app.md 已登记):重试采集
    import time as _sleep

    tree = []
    for _ in range(4):
        tree_snap = http.post("/api/v1/snapshots", json={"with_tree": True}).json()
        if tree_snap.get("snapshot_id") and tree_snap.get("tree_error") is None:
            tree = http.get(
                f"/api/v1/snapshots/{tree_snap['snapshot_id']}/tree"
            ).json()["nodes"]
            if any(n["text"] == "WLAN" for n in tree):
                break
        _sleep.sleep(1.5)
    wlans = [n for n in tree if n["text"] == "WLAN" and n["bounds"]]
    assert len(wlans) == 1, f"冷启动后主页 WLAN 行应唯一,得到 {len(wlans)}"
    wlan = wlans[0]
    bx = wlan["bounds"]
    # 框选整行(图标 + 文本),模拟人工在画面上拖动
    region = [0, max(0, bx[1] - 10), bx[2] + 60, (bx[3] - bx[1]) + 20]

    # 唯一逻辑名:资产库跨运行持久,同名会走更新分支;时间戳名让每轮都是新建
    import time as _time

    logical = f"studio/wlan-row-{_time.time_ns() % 10_000_000}.png"
    body = {"snapshot_id": snap["snapshot_id"], "logical": logical, "region": region}
    made = http.post("/api/v1/templates", json=body)
    if made.status_code != 200:  # 同名或其他冲突:走确认更新
        listed = http.get("/api/v1/templates").json()["templates"]
        existing = next((t for t in listed if t["logical"] == logical), None)
        if existing is None:
            pytest.fail(f"模板创建失败且无同名记录:{made.text}")
        made = http.put(
            f"/api/v1/templates/{logical}",
            json={**body, "expected_old_sha256": existing["image_sha256"]},
        )
    assert made.status_code == 200, made.text
    created = made.json()

    # 3. 验证:重新采集匹配,唯一或多数命中,分数可读
    validated = http.post(
        "/api/v1/templates/validate",
        json={"target": {"by": "image", "value": logical}},
    )
    assert validated.status_code == 200
    result = validated.json()
    assert result["count"] >= 1, f"模板应至少命中一次:{result}"

    # 4. 复制的表达式内容即 Target 构造参数:直接执行点击
    target = Target(Locator.IMAGE, logical)
    import asyncio

    asyncio.run(adapter.click(target))

    # 5. 后置业务断言:进入 WLAN 子页
    advanced = Target(Locator.TEXT, "网络加速")
    assert asyncio.run(adapter.wait_for(advanced)) is True
    assert created["width"] == region[2] and created["height"] == region[3]
