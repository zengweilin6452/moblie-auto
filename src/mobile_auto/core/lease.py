"""跨进程设备租约:Windows 文件区域锁(plan 7.2)。

锁文件保留在用户本机统一目录下,以设备序列号摘要命名(不泄露序列号);
锁是 OS 句柄的生命周期:释放仅解锁句柄、不删除文件,进程退出由操作系统
释放,不靠删除锁文件或 PID 过期时间偷锁,不提供强制抢占。
保证范围为同一 Windows 登录用户。
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from ..errors import DeviceBusy, DeviceUnavailable

DEFAULT_LEASE_DIR = Path.home() / ".mobile-auto" / "leases"


class DeviceLease:
    """某一时刻允许向设备发送操作的跨进程独占权(GLOSSARY“设备租约”)。"""

    def __init__(self, serial: str, lease_dir: Path | None = None) -> None:
        self._serial = serial
        self._dir = Path(lease_dir) if lease_dir else DEFAULT_LEASE_DIR
        digest = hashlib.sha256(serial.encode("utf-8")).hexdigest()[:24]
        self._path = self._dir / f"{digest}.lock"
        self._handle = None  # OS 句柄;非空即持有租约
        self._mutex = threading.Lock()  # 进程内并发保护

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        """非阻塞获取;被其他进程持有时立即报 DeviceBusy,不排队。"""
        with self._mutex:
            if self._handle is not None:
                return
            if os.name != "nt":
                raise DeviceUnavailable("设备租约当前仅支持 Windows(plan 7.2)")
            import msvcrt

            self._dir.mkdir(parents=True, exist_ok=True)
            handle = open(self._path, "a+b")
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                handle.close()
                raise DeviceBusy(
                    f"设备 {self._serial} 正被其他进程占用,不排队抢占"
                ) from exc
            self._handle = handle

    def release(self) -> None:
        """释放租约;幂等。释放不回滚已发送的操作(GLOSSARY)。"""
        with self._mutex:
            if self._handle is None:
                return
            import msvcrt

            try:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # 区域已被 OS 随句柄处置时忽略;句柄关闭兜底
            finally:
                self._handle.close()
                self._handle = None

    def __enter__(self) -> DeviceLease:
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
