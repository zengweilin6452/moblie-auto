"""集成测试：mobile_perf DeviceGate 适配器（ADR-0001 依赖反转）。

真实文件锁路径：PerfDeviceGate 把本包 DeviceLease 桥接为 mobile_perf
的短事务 DeviceGate 协议——宿主整生命周期租约覆盖时嵌入模式重入放行；
独立短事务借用被其他进程占用时转换为 mobile_perf 的 GateBusyError
（fast-fail 语义不变）。不碰真机。
"""

import pytest

pytest.importorskip("mobile_perf")  # 未安装 mobile_perf:整模块跳过,不阻断收集

from mobile_perf.gate import GateBusyError  # noqa: E402

from mobile_auto._perf_gate import PerfDeviceGate  # noqa: E402
from mobile_auto.core.lease import DeviceLease  # noqa: E402
from mobile_auto.errors import DeviceBusy  # noqa: E402


def test_borrow_acquires_and_releases_short_transaction(tmp_path):
    gate = PerfDeviceGate(lease_dir=tmp_path)
    probe = DeviceLease("SERIAL-P", lease_dir=tmp_path)

    with gate.borrow("SERIAL-P", purpose="mobile_perf.start"):
        with pytest.raises(DeviceBusy):
            probe.acquire()  # 适配器借用时设备被占（OS 区域锁生效）

    probe.acquire()  # 借用退出即释放；不排队，重新发起即可获得
    probe.release()


def test_borrow_converts_device_busy_to_gate_busy(tmp_path):
    holder = DeviceLease("SERIAL-P", lease_dir=tmp_path)
    holder.acquire()
    try:
        gate = PerfDeviceGate(lease_dir=tmp_path)
        with pytest.raises(GateBusyError):
            gate.borrow("SERIAL-P", purpose="mobile_perf.stop_pull")
    finally:
        holder.release()


def test_embed_mode_passthrough_when_host_lease_held(tmp_path):
    """宿主（DeviceSession）整生命周期租约覆盖时：重入放行、不额外取锁。"""
    host_lease = DeviceLease("SERIAL-P", lease_dir=tmp_path)
    host_lease.acquire()
    try:
        gate = PerfDeviceGate(leases={"SERIAL-P": host_lease})
        probe = DeviceLease("SERIAL-P", lease_dir=tmp_path)
        with gate.borrow("SERIAL-P", purpose="mobile_perf.start"):
            assert host_lease.held
            with pytest.raises(DeviceBusy):
                probe.acquire()  # 锁仍由宿主租约持有（未被适配器动过）
        assert host_lease.held  # 借用退出不影响宿主租约
    finally:
        host_lease.release()
