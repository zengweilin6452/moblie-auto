"""mobile_perf DeviceGate 适配器（ADR-0001 依赖方向：mobile_auto → mobile_perf）。

把本包整生命周期持有的 DeviceLease 桥接为 mobile_perf 的短事务
DeviceGate 协议：

- 嵌入模式：宿主已持租约（DeviceSession 存续期，经 ``leases`` 预置）时，
  borrow 重入放行——序列化由宿主会话队列保证，适配器不触碰租约；
- 独立模式：宿主未持租约时按短事务 acquire/release（完成即还）；
- 被其他进程占用时 DeviceBusy 转换为 mobile_perf 的 GateBusyError，
  fast-fail 语义与组件默认 FileLockGate 一致。

注入方式：``PerfKit(gate=PerfDeviceGate(leases={serial: session._lease}))``。
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Protocol

from mobile_perf.gate import GateBusyError

from .core.lease import DeviceLease
from .errors import DeviceBusy


class _ReleaseOnly(Protocol):
    def release(self) -> None: ...


class PerfDeviceGate:
    """实现 mobile_perf.gate.DeviceGate 协议（短事务借用）。

    ``leases`` 预置宿主已持有的租约（键为 serial），命中的 serial 走嵌入
    模式重入放行；未命中时创建新 DeviceLease 做短事务借用。
    """

    def __init__(
        self, leases: dict[str, DeviceLease] | None = None, *, lease_dir=None
    ) -> None:
        self._leases: dict[str, DeviceLease] = dict(leases or {})
        self._lease_dir = lease_dir

    def borrow(
        self, serial: str, *, purpose: str, timeout_s: float = 10.0
    ) -> AbstractContextManager[None]:
        lease = self._leases.get(serial)
        if lease is not None and lease.held:
            return nullcontext()  # 嵌入模式：宿主租约覆盖，重入放行
        short_lease = DeviceLease(serial, lease_dir=self._lease_dir, purpose=purpose)
        try:
            short_lease.acquire()
        except DeviceBusy as exc:
            raise GateBusyError(
                f"设备 {serial} 正被其他进程占用（purpose={purpose}），fast-fail 不重试"
            ) from exc
        return _ShortBorrow(short_lease)


class _ShortBorrow:
    """短事务借用令牌：退出释放本适配器获取的租约（不动宿主租约）。"""

    def __init__(self, lease: _ReleaseOnly):
        self._lease = lease

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc_info) -> None:
        self._lease.release()


__all__ = ["PerfDeviceGate"]
