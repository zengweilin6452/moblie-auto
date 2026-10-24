"""图像模板制作闭环的 API 外部行为(工单 11)。"""

import cv2
import numpy as np

PATTERN = np.random.default_rng(7).integers(0, 255, size=(30, 30, 3), dtype=np.uint8)


def test_create_template_crops_server_snapshot(client):
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={}).json()
    res = http.post(
        "/api/v1/templates",
        json={
            "snapshot_id": snap["snapshot_id"],
            "logical": "studio/target.png",
            "region": [10, 20, 30, 30],
            "note": "unit",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["logical"] == "studio/target.png"
    assert body["width"] == 30 and body["height"] == 30
    assert len(body["sha256"]) == 64

    image = http.get("/api/v1/templates/studio/target.png/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert image.content != snap  # 服务端快照裁剪,不是浏览器上传

    listed = http.get("/api/v1/templates").json()["templates"]
    assert any(t["logical"] == "studio/target.png" for t in listed)


def test_create_writes_capture_center_from_region(client):
    """P4b:studio 登记时自动写入采集中心(区域中心相对快照中心的归一化偏移)。"""
    from mobile_auto.imaging.store import TemplateStore

    http, session, _ = client
    snap = http.post("/api/v1/snapshots", json={}).json()
    http.post(
        "/api/v1/templates",
        json={
            "snapshot_id": snap["snapshot_id"],
            "logical": "studio/prior.png",
            "region": [10, 20, 30, 30],  # 快照 150x200:中心 (25, 35)
        },
    )
    meta = TemplateStore(session.config.assets_dir).load("studio/prior.png").meta
    assert meta["capture_center"] == [-0.25, -0.325]  # ((25-75)/200, (35-100)/200)


def test_create_duplicate_conflict_then_confirmed_update(client):
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={}).json()
    body = {
        "snapshot_id": snap["snapshot_id"],
        "logical": "studio/dup.png",
        "region": [5, 5, 20, 20],
    }
    first = http.post("/api/v1/templates", json=body)
    assert first.status_code == 200
    old_sha = first.json()["sha256"]

    dup = http.post("/api/v1/templates", json=body)
    assert dup.status_code == 409  # 同名创建是冲突;前端以此触发确认更新流程
    assert dup.json()["code"] == "DuplicateTemplate"

    # 更新用同一截图的另一块区域:内容不同 → 新版本摘要不同
    updated = http.put(
        "/api/v1/templates/studio/dup.png",
        json={**body, "expected_old_sha256": old_sha, "region": [50, 80, 20, 20]},
    )
    assert updated.status_code == 200
    assert updated.json()["sha256"] != old_sha

    stale = http.put(
        "/api/v1/templates/studio/dup.png",
        json={**body, "expected_old_sha256": old_sha},  # 旧摘要已过期
    )
    assert stale.status_code == 409  # 摘要冲突


def test_create_with_bad_region_422_and_unknown_snapshot_404(client):
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={}).json()
    out = http.post(
        "/api/v1/templates",
        json={"snapshot_id": snap["snapshot_id"], "logical": "a.png",
                "region": [140, 190, 50, 50]},
    )
    assert out.status_code == 422
    missing = http.post(
        "/api/v1/templates",
        json={"snapshot_id": "nope", "logical": "a.png", "region": [0, 0, 10, 10]},
    )
    assert missing.status_code == 404


def test_validate_template_reports_scores_and_boxes(client):
    """真实引擎链路:截图含图案 → 制作模板 → 新采集上重匹配,唯一命中带分数。"""
    http, _, backend = client
    # 后端截图含图案于 (40,60):制作模板与验证都从真实像素出发
    screen = np.random.default_rng(11).integers(0, 255, size=(200, 150, 3), dtype=np.uint8)
    screen[60:90, 40:70] = PATTERN
    ok, buf = cv2.imencode(".png", screen[:, :, ::-1])
    backend.png = buf.tobytes()

    snap = http.post("/api/v1/snapshots", json={}).json()
    made = http.post(
        "/api/v1/templates",
        json={
            "snapshot_id": snap["snapshot_id"],
            "logical": "studio/real.png",
            "region": [40, 60, 30, 30],
        },
    )
    assert made.status_code == 200, made.text

    res = http.post(
        "/api/v1/templates/validate",
        json={"target": {"by": "image", "value": "studio/real.png"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1 and body["passed"] is True
    assert body["matches"][0]["x"] == 40 and body["matches"][0]["y"] == 60
    assert body["scores"] and body["scores"][0] > 0.9


def test_validate_rejects_non_image_target(client):
    http, _, _ = client
    res = http.post(
        "/api/v1/templates/validate",
        json={"target": {"by": "text", "value": "WLAN"}},
    )
    assert res.status_code == 422
