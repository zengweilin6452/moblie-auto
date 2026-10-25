"""占用与运行态端点单元测试(P2 工单 07):伪造租约与状态文件。"""

import json

import pytest

pytest.importorskip("fastapi", reason="viewer extra 未安装")
from conftest import ViewerBackend  # noqa: E402
from fakes import NullLease  # noqa: E402

import mobile_auto.core.lease as lease_mod  # noqa: E402
from mobile_auto import DeviceSession, MobileConfig  # noqa: E402
from mobile_auto.viewer.app import create_app  # noqa: E402
from mobile_auto.viewer.store import SnapshotStore  # noqa: E402


class FakeLeaseProbe:
    held_serials: set = set()

    def __init__(self, serial, *args, **kwargs):
        self.serial = serial

    def probe_held(self):
        return self.serial in FakeLeaseProbe.held_serials


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "gw0.json").write_text(
        json.dumps(
            {
                "worker": "gw0",
                "phase": "running",
                "serial": "SERIAL1",
                "current_test": "test_a.py::test_x",
                "pid": 111,
                "heartbeat": "2999-01-01T00:00:00.000+00:00",
            }
        ),
        encoding="utf-8",
    )
    session = DeviceSession(
        MobileConfig(device_serial="SERIAL1", artifacts_dir=tmp_path),
        backend_factory=lambda: ViewerBackend(),
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    session.release_lease()
    FakeLeaseProbe.held_serials = {"SERIAL1"}
    monkeypatch.setattr(lease_mod, "DeviceLease", FakeLeaseProbe)
    monkeypatch.setattr(
        lease_mod,
        "read_holder",
        lambda serial, lease_dir=None: (
            {"worker": "gw0", "hostname": "h", "pid": 111, "purpose": "pool"}
            if serial == "SERIAL1"
            else None
        ),
    )
    from fastapi.testclient import TestClient

    app = create_app(session, SnapshotStore())
    with TestClient(app) as client:
        yield client
    session.close()


def test_leases_reports_held_with_holder(app_client):
    data = app_client.get("/api/v1/leases").json()
    entry = next(lease for lease in data["leases"] if lease["serial"] == "SERIAL1")
    assert entry["held"] is True
    assert entry["holder"]["worker"] == "gw0"


def test_leases_free_device_has_no_holder(app_client):
    FakeLeaseProbe.held_serials = set()
    data = app_client.get("/api/v1/leases").json()
    entry = next(lease for lease in data["leases"] if lease["serial"] == "SERIAL1")
    assert entry["held"] is False
    assert entry["holder"] is None  # 空闲不展示残留持有者


def test_runs_lists_states_with_stale_flag(app_client):
    data = app_client.get("/api/v1/runs").json()
    assert len(data["runs"]) == 1
    run = data["runs"][0]
    assert run["current_test"] == "test_a.py::test_x"
    assert run["stale"] is False
