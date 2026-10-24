"""集成测试:跨进程租约争用(plan 12.1 并发域)。

真实两进程争用同一设备的文件租约;取消与竞态的确定性验证在
单元层(注入伪造后端)完成,这里验证真实文件锁路径。
"""

import subprocess
import sys
from pathlib import Path

from mobile_auto.lease import DeviceLease

_CHILD = """
import sys
from mobile_auto.lease import DeviceLease

lease = DeviceLease(sys.argv[1], lease_dir=sys.argv[2])
try:
    lease.acquire()
    print("ACQUIRED", flush=True)
    import time
    time.sleep(3)
    lease.release()
    print("RELEASED", flush=True)
except Exception as exc:
    print(f"ERROR: {type(exc).__name__}", flush=True)
"""


def test_two_process_lease_contention(tmp_path):
    """第二进程在租约持有期间请求忙设备:立即 busy,不排队抢占。"""
    lease_dir = tmp_path / "leases"
    lease = DeviceLease("SERIAL-INTG", lease_dir=lease_dir)
    lease.acquire()  # 本进程持有
    try:
        child = subprocess.run(
            [sys.executable, "-c", _CHILD, "SERIAL-INTG", str(lease_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path(__file__).parents[2],
        )
        assert "ACQUIRED" not in child.stdout
        assert "ERROR: DeviceBusy" in child.stdout
    finally:
        lease.release()

    # 释放后子进程可获得租约(不排队:重新发起请求)
    child2 = subprocess.run(
        [sys.executable, "-c", _CHILD, "SERIAL-INTG", str(lease_dir)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parents[2],
    )
    assert "ACQUIRED" in child2.stdout and "RELEASED" in child2.stdout
