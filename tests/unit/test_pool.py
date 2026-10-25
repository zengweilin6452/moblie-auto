"""设备池获取与运行状态单元测试(P2 工单 05):注入伪造枚举与租约。"""

import json

import pytest

from mobile_auto import DeviceBusy, MobileConfig
from mobile_auto.pool import (
    acquire_from_pool,
    read_run_states,
    worker_artifacts_dir,
    write_run_state,
)


class FakeLease:
    def __init__(self, serial, worker="", busy=False):
        self.serial = serial
        self.busy = busy
        self.acquired = 0

    def acquire(self):
        if self.busy:
            raise DeviceBusy(f"{self.serial} 被占用")
        self.acquired += 1


def make_world(states, busy=frozenset()):
    leases = {}

    def factory(serial, worker=""):
        leases[serial] = FakeLease(serial, worker, busy=serial in busy)
        return leases[serial]

    return (lambda: dict(states)), factory, leases


# ── 配置 ─────────────────────────────────────────────────


def test_config_device_pool_dedupe_and_validate():
    config = MobileConfig(device_pool=["B", "A", "B", " A "])
    assert config.device_pool == ("B", "A")

    with pytest.raises(Exception, match="device_pool"):
        MobileConfig(device_pool=["", "A"])
    with pytest.raises(Exception, match="device_pool"):
        MobileConfig(device_pool="A")


def test_config_toml_roundtrip(tmp_path):
    from mobile_auto.config import load_config

    toml = tmp_path / "local.toml"
    toml.write_text('device_pool = ["S1", "S2"]\n', encoding="utf-8")
    assert load_config(toml).device_pool == ("S1", "S2")

    toml.write_text('device_pool = []\n', encoding="utf-8")
    assert load_config(toml).device_pool == ()


# ── 池获取 ───────────────────────────────────────────────


def test_acquire_picks_first_free_authorized():
    enumerator, factory, leases = make_world(
        {"S1": "device", "S2": "device", "OFF": "offline"}
    )
    serial, lease = acquire_from_pool(
        ["S1", "S2"], enumerator=enumerator, lease_factory=factory
    )
    assert serial == "S1" and lease.acquired == 1


def test_acquire_skips_busy_and_offline():
    enumerator, factory, leases = make_world(
        {"S1": "device", "S2": "device", "BAD": "unauthorized"},
        busy={"S1"},
    )
    serial, lease = acquire_from_pool(
        ["S1", "BAD", "S2"], enumerator=enumerator, lease_factory=factory
    )
    assert serial == "S2"  # 被占/未授权都跳过,不静默用池外设备


def test_acquire_times_out_with_clear_error_when_all_busy():
    enumerator, factory, _ = make_world({"S1": "device"}, busy={"S1"})
    with pytest.raises(DeviceBusy, match="在线:\\['S1'\\]"):
        acquire_from_pool(
            ["S1"],
            enumerator=enumerator,
            lease_factory=factory,
            wait_seconds=0.05,
            poll_interval=0.02,
        )


def test_acquire_empty_pool_raises_immediately():
    with pytest.raises(DeviceBusy, match="设备池为空"):
        acquire_from_pool([], enumerator=lambda: {})


def test_acquire_retries_until_device_freed():
    states = {"S1": "device"}
    enumerator, factory, leases = make_world(states)
    leases["S1"] = FakeLease("S1", busy=True)
    # 用会翻转为空闲的枚举:首次忙,重试时已释放
    attempts = {"n": 0}
    original = factory

    def flipping_factory(serial, worker=""):
        lease = original(serial, worker)
        if attempts["n"] == 0:
            lease.busy = True
            attempts["n"] += 1
        return lease

    serial, lease = acquire_from_pool(
        ["S1"],
        enumerator=enumerator,
        lease_factory=flipping_factory,
        wait_seconds=2,
        poll_interval=0.02,
    )
    assert serial == "S1"


# ── 产物目录与运行状态 ────────────────────────────────────


def test_worker_artifacts_dir_split():
    from pathlib import Path

    base = Path("art")
    assert worker_artifacts_dir(base, "") == Path("art")
    assert worker_artifacts_dir(base, "gw0") == Path("art") / "xdist-gw0"


def test_run_state_roundtrip_and_stale(tmp_path):
    runs = tmp_path / "runs"
    write_run_state(runs, worker="gw0", phase="running", serial="S1",
                    current_test="test_a.py::test_x")
    write_run_state(runs, worker="main", phase="finished", serial="S2")
    (runs / "broken.json").write_text("{half", encoding="utf-8")
    states = read_run_states(runs)
    by_worker = {s["worker"]: s for s in states}
    assert by_worker["gw0"]["serial"] == "S1"
    assert by_worker["gw0"]["current_test"] == "test_a.py::test_x"
    assert by_worker["gw0"]["stale"] is False
    assert by_worker["main"]["phase"] == "finished"
    assert len(states) == 2  # 坏文件跳过


def test_run_state_stale_when_heartbeat_old(tmp_path):
    runs = tmp_path / "runs"
    write_run_state(runs, worker="gw1", phase="running")
    state_file = runs / "gw1.json"
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    payload["heartbeat"] = "2020-01-01T00:00:00.000+00:00"
    state_file.write_text(json.dumps(payload), encoding="utf-8")
    assert read_run_states(runs)[0]["stale"] is True
