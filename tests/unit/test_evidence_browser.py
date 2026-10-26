"""证据浏览端点单元测试(P2 工单 08):伪造 artifacts,验证只读与防穿越。"""

import json

import pytest

pytest.importorskip("fastapi", reason="viewer extra 未安装")

from conftest import ViewerBackend  # noqa: E402
from fakes import NullLease  # noqa: E402

from mobile_auto import DeviceSession, MobileConfig  # noqa: E402
from mobile_auto.viewer.app import create_app  # noqa: E402
from mobile_auto.viewer.store import SnapshotStore  # noqa: E402

EVIDENCE_ID = "0123456789abcdef"


@pytest.fixture
def app_with_evidence(tmp_path):
    evidence_dir = tmp_path / "evidence" / EVIDENCE_ID
    evidence_dir.mkdir(parents=True)
    meta = {
        "evidence_id": EVIDENCE_ID,
        "created_at": "2026-10-25T10:00:00",
        "failure": {"type": "AssertionError", "message": "故意失败"},
        "device": {"serial": "SERIAL1", "app_package": "demo"},
        "frame_source": {"source": "scrcpy-video"},
        "artifacts": [{"name": "failure-frame.png", "kind": "post-failure-frame"}],
        "not_captured": [],
    }
    (evidence_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    (evidence_dir / "failure-frame.png").write_bytes(b"\x89PNG-fake")
    (evidence_dir / "secret.txt").write_bytes(b"not registered")

    session = DeviceSession(
        MobileConfig(device_serial="SERIAL1", artifacts_dir=tmp_path),
        backend_factory=lambda: ViewerBackend(),
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    session.release_lease()
    app = create_app(session, SnapshotStore(), channel_manager=None)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client
    session.close()


def test_list_returns_registered_evidence(app_with_evidence):
    data = app_with_evidence.get("/api/v1/evidence").json()
    assert len(data["evidence"]) == 1
    item = data["evidence"][0]
    assert item["evidence_id"] == EVIDENCE_ID
    assert item["failure"] == "AssertionError"
    assert item["frame_source"] == "scrcpy-video"


def test_detail_returns_full_meta(app_with_evidence):
    res = app_with_evidence.get(f"/api/v1/evidence/{EVIDENCE_ID}")
    assert res.status_code == 200
    assert res.json()["device"]["serial"] == "SERIAL1"


def test_registered_file_served_with_media_type(app_with_evidence):
    res = app_with_evidence.get(f"/api/v1/evidence/{EVIDENCE_ID}/files/failure-frame.png")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")
    assert res.content.startswith(b"\x89PNG")


def test_unregistered_and_traversal_names_rejected(app_with_evidence):
    for name in ("secret.txt", "../meta.json", "..%2Fmeta.json", "nope.png"):
        res = app_with_evidence.get(
            f"/api/v1/evidence/{EVIDENCE_ID}/files/{name}"
        )
        assert res.status_code == 404, name


def test_malformed_id_rejected(app_with_evidence):
    res = app_with_evidence.get("/api/v1/evidence/not-hex!")
    assert res.status_code == 404


def test_empty_artifacts_dir_is_ok(tmp_path):
    session = DeviceSession(
        MobileConfig(device_serial="S", artifacts_dir=tmp_path / "empty"),
        backend_factory=lambda: ViewerBackend(),
        device_enumerator=lambda: {"S": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    session.release_lease()
    app = create_app(session, SnapshotStore())
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        data = client.get("/api/v1/evidence").json()
    session.close()
    assert data["evidence"] == []


WORKER_EVIDENCE_ID = "fedcba9876543210"


def test_journal_endpoint_reads_worker_journal(app_with_evidence, tmp_path):
    worker = tmp_path / "xdist-gw7"
    evidence_dir = worker / "evidence" / WORKER_EVIDENCE_ID
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "meta.json").write_text(
        json.dumps(
            {
                "evidence_id": WORKER_EVIDENCE_ID,
                "created_at": "2026-10-25T11:00:00",
                "device": {"serial": "SERIAL1"},
                "artifacts": [],
                "not_captured": [],
                "journal_summary": {"steps": 2, "first_failure": None},
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {"ts": "2026-10-25T11:00:00.001Z", "op": "goto", "outcome": "ok", "duration_ms": 1.0},
        {"ts": "2026-10-25T11:00:00.002Z", "op": "click", "outcome": "ok", "duration_ms": 2.0},
        {"op": "journal", "outcome": "truncated"},
    ]
    (worker / "actions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    res = app_with_evidence.get(f"/api/v1/evidence/{WORKER_EVIDENCE_ID}/journal")
    assert res.status_code == 200
    data = res.json()
    assert [line["op"] for line in data["lines"]] == ["goto", "click", "journal"]
    assert data["total"] == 3
    assert data["truncated"] is True


def test_journal_endpoint_404_without_journal(app_with_evidence):
    res = app_with_evidence.get(f"/api/v1/evidence/{EVIDENCE_ID}/journal")
    assert res.status_code == 404
    assert res.json()["code"] == "not-found"
    assert app_with_evidence.get("/api/v1/evidence/not-hex!/journal").status_code == 404
