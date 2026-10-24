"""原生拾取与定位验证的 API 外部行为(工单 10)。"""



def test_pick_returns_candidates_deepest_first(client):
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={"with_tree": True}).json()
    # TextView WLAN bounds [10,20][80,40]:点 (30,30) 命中 TextView 与两层容器
    res = http.post(
        "/api/v1/pick",
        json={"snapshot_id": snap["snapshot_id"], "x": 30, "y": 30},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overlap"] is True  # 重叠候选由用户选择,不静默选节点
    classes = [c["class_name"] for c in body["candidates"]]
    assert classes[0] == "android.widget.TextView"  # 最深(最具体)节点在前
    locators = body["candidates"][0]["locators"]
    modes = {c["by"] for c in locators}
    assert {"resource_id", "text", "u2_selector", "xpath"} <= modes
    for c in locators:
        if c["by"] == "text":
            assert "文本易变" in c["risk"]  # 易变属性标注风险


def test_pick_without_tree_reports_capability_disabled(client):
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={"with_tree": False}).json()
    res = http.post(
        "/api/v1/pick",
        json={"snapshot_id": snap["snapshot_id"], "x": 10, "y": 10},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "capability-unavailable"


def test_pick_out_of_range_422(client):
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={"with_tree": True}).json()
    res = http.post(
        "/api/v1/pick",
        json={"snapshot_id": snap["snapshot_id"], "x": 9999, "y": 9999},
    )
    assert res.status_code == 422


def test_validate_native_unique_passes(client):
    http, _, _ = client
    res = http.post(
        "/api/v1/locators/validate",
        json={"target": {"by": "text", "value": "WLAN"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1 and body["passed"] is True
    assert body["matches"][0]["width"] > 0
    assert "唯一匹配" in body["note"]


def test_validate_zero_and_multi_not_passed(client):
    http, _, _ = client
    zero = http.post(
        "/api/v1/locators/validate",
        json={"target": {"by": "text", "value": "不存在的文本"}},
    ).json()
    assert zero["count"] == 0 and zero["passed"] is False
    assert "零匹配" in zero["note"]

    multi = http.post(
        "/api/v1/locators/validate",
        json={"target": {"by": "xpath", "value": "//node"}},
    ).json()
    assert multi["count"] > 1 and multi["passed"] is False
    assert "多匹配" in multi["note"]  # 明确提示,不显示为"验证通过"


def test_validate_expression_roundtrip_same_rule(client):
    """拾取候选的 target 字段构造(即表达式内容)验证同规则直达。"""
    http, _, _ = client
    snap = http.post("/api/v1/snapshots", json={"with_tree": True}).json()
    pick = http.post(
        "/api/v1/pick",
        json={"snapshot_id": snap["snapshot_id"], "x": 30, "y": 30},
    ).json()
    candidate = pick["candidates"][0]["locators"][0]
    res = http.post(
        "/api/v1/locators/validate", json={"target": candidate["target"]}
    )
    body = res.json()
    assert body["count"] == 1 and body["passed"] is True


def test_validate_invalid_target_422(client):
    http, _, _ = client
    res = http.post(
        "/api/v1/locators/validate",
        json={"target": {"by": "css", "value": "#x"}},
    )
    assert res.status_code == 422
    assert res.json()["code"] == "invalid-target"
