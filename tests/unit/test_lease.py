"""跨进程设备租约原语:文件区域锁的获取、竞争与释放(P2 工单 04:持有者信息)。"""

import json

import pytest

from mobile_auto import DeviceBusy
from mobile_auto.core.lease import DEFAULT_LEASE_DIR, DeviceLease, read_holder


def test_acquire_release_and_reacquire(tmp_path):
    lease = DeviceLease("SERIAL-X", lease_dir=tmp_path)
    assert not lease.held
    lease.acquire()
    assert lease.held
    lease.release()
    assert not lease.held
    lease.acquire()  # 释放后可重新获取
    assert lease.held
    lease.release()


def test_release_is_idempotent(tmp_path):
    lease = DeviceLease("SERIAL-X", lease_dir=tmp_path)
    lease.release()  # 未持有也安全
    lease.acquire()
    lease.release()
    lease.release()


def test_second_lease_same_serial_is_busy_immediately(tmp_path):
    first = DeviceLease("SERIAL-X", lease_dir=tmp_path)
    first.acquire()
    second = DeviceLease("SERIAL-X", lease_dir=tmp_path)
    with pytest.raises(DeviceBusy, match="不排队抢占"):
        second.acquire()
    assert not second.held
    first.release()
    second.acquire()  # 释放后立即可获得
    assert second.held
    second.release()


def test_lock_file_named_by_digest_not_serial(tmp_path):
    lease = DeviceLease("SECRET-SERIAL-123", lease_dir=tmp_path)
    lease.acquire()
    try:
        files = list(tmp_path.glob("*.lock"))
        assert len(files) == 1
        name = files[0].name
        assert "SECRET-SERIAL-123" not in name  # 不泄露序列号
        assert len(name) < 40
        assert DeviceLease("SECRET-SERIAL-123", lease_dir=tmp_path).path == files[0]
    finally:
        lease.release()


def test_default_dir_is_user_level():
    assert DEFAULT_LEASE_DIR == Path_home() / ".mobile-auto" / "leases"


def Path_home():
    from pathlib import Path

    return Path.home()


# ── 持有者信息(P2 工单 04)───────────────────────────────


def test_acquire_writes_holder_info(tmp_path):
    lease = DeviceLease(
        "SERIAL-H", lease_dir=tmp_path, worker="gw0", purpose="pool"
    )
    lease.acquire()
    try:
        holder = read_holder("SERIAL-H", lease_dir=tmp_path)
        assert holder is not None
        import os
        import socket

        assert holder["pid"] == os.getpid()
        assert holder["hostname"] == socket.gethostname()
        assert holder["worker"] == "gw0"
        assert holder["purpose"] == "pool"
        assert "T" in holder["acquired_at"]  # ISO 时间
    finally:
        lease.release()


def test_reacquire_overwrites_stale_holder(tmp_path):
    first = DeviceLease("SERIAL-H", lease_dir=tmp_path, worker="gw0")
    first.acquire()
    first.release()  # 释放不擦除:旧信息残留
    stale = read_holder("SERIAL-H", lease_dir=tmp_path)
    assert stale is not None and stale["worker"] == "gw0"

    second = DeviceLease("SERIAL-H", lease_dir=tmp_path, worker="gw1")
    second.acquire()
    try:
        assert read_holder("SERIAL-H", lease_dir=tmp_path)["worker"] == "gw1"
    finally:
        second.release()


def test_read_holder_tolerates_garbage_and_missing(tmp_path):
    assert read_holder("NO-SUCH", lease_dir=tmp_path) is None
    lease = DeviceLease("GARBAGE", lease_dir=tmp_path)
    lease.holder_path.write_text("{half-written json", encoding="utf-8")
    assert read_holder("GARBAGE", lease_dir=tmp_path) is None


def test_probe_held_reflects_cross_lease_state(tmp_path):
    first = DeviceLease("SERIAL-P", lease_dir=tmp_path)
    second = DeviceLease("SERIAL-P", lease_dir=tmp_path)
    assert not first.probe_held()  # 无锁文件
    first.acquire()
    try:
        assert first.probe_held()  # 自己持有
        assert second.probe_held()  # 他者视角:被持有
        assert not second.held  # 探测不改变持有权
    finally:
        first.release()
    assert not second.probe_held()  # 释放后回落空闲


def test_holder_sidecar_isolated_from_lock_file(tmp_path):
    """持有者信息写伴生 JSON,锁文件不做任何读写(实测持锁期间锁文件的
    任何 I/O 都被 Windows 拒绝);互斥仍由锁文件字节 0 决定。"""
    lease = DeviceLease("SERIAL-I", lease_dir=tmp_path, worker="gw0")
    lease.acquire()
    try:
        assert lease.holder_path.is_file()
        holder = json.loads(lease.holder_path.read_text(encoding="utf-8"))
        assert holder["worker"] == "gw0"
        other = DeviceLease("SERIAL-I", lease_dir=tmp_path)
        with pytest.raises(DeviceBusy):
            other.acquire()
    finally:
        lease.release()
