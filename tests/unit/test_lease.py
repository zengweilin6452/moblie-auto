"""跨进程设备租约原语:文件区域锁的获取、竞争与释放。"""

import pytest

from mobile_auto import DeviceBusy
from mobile_auto.lease import DEFAULT_LEASE_DIR, DeviceLease


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
