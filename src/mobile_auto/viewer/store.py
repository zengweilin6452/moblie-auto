"""快照库:实时快照上限、有效期与编辑期冻结固定引用(plan 8.3)。

实时查看快照最多 3 份、总编码数据最多 64 MiB、有效期 120 秒;
进入拾取/模板编辑的冻结快照被固定引用,不受上限驱逐,
直到退出编辑或冻结超时(10 分钟,校准初值)。驱逐不删除已保存
模板或证据。单张上限(1200 万像素 / 20 MiB)在写入入口校验。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

MAX_LIVE_SNAPSHOTS = 3
MAX_TOTAL_BYTES = 64 * 1024 * 1024
LIVE_TTL_SECONDS = 120.0
FROZEN_TTL_SECONDS = 600.0
MAX_PIXELS = 12_000_000
MAX_INPUT_BYTES = 20 * 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Snapshot:
    """一次采集绑定的一组画面与树(GLOSSARY"冻结快照");成功后不再变化。"""

    png: bytes
    width: int
    height: int
    orientation: str
    captured_at: str
    tree_json: str | None = None
    tree_captured_at: str | None = None
    tree_error: str | None = None
    tree_root: object | None = None  # 解析后的 UiNode 根,仅供拾取;不入响应
    frozen: bool = False
    frozen_at: datetime | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def expired(self, now: datetime) -> bool:
        if self.frozen:
            if self.frozen_at is None:
                return True
            return now - self.frozen_at > timedelta(seconds=FROZEN_TTL_SECONDS)
        captured = datetime.fromisoformat(self.captured_at)
        return now - captured > timedelta(seconds=LIVE_TTL_SECONDS)


class SnapshotStore:
    """内存快照库;线程安全。驱逐只作用于未冻结的实时快照。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snaps: dict[str, Snapshot] = {}

    def add(self, snapshot: Snapshot) -> str:
        if snapshot.width * snapshot.height > MAX_PIXELS:
            raise ValueError(
                f"快照 {snapshot.width}x{snapshot.height} 超过单张 "
                f"{MAX_PIXELS} 像素上限"
            )
        if len(snapshot.png) > MAX_INPUT_BYTES:
            raise ValueError(
                f"快照编码数据超过单张 {MAX_INPUT_BYTES} 字节上限"
            )
        with self._lock:
            self._purge_locked()
            self._snaps[snapshot.id] = snapshot
            self._evict_locked()
        return snapshot.id

    def get(self, snapshot_id: str) -> Snapshot | None:
        with self._lock:
            self._purge_locked()
            return self._snaps.get(snapshot_id)

    def freeze(self, snapshot_id: str, frozen: bool) -> Snapshot | None:
        """编辑期固定引用:冻结不受份数/总量驱逐;退出编辑或超时后释放。"""
        with self._lock:
            self._purge_locked()
            snap = self._snaps.get(snapshot_id)
            if snap is None:
                return None
            snap.frozen = frozen
            snap.frozen_at = _utc_now() if frozen else None
            if not frozen:
                self._evict_locked()
            return snap

    def snapshot_ids(self) -> list[str]:
        with self._lock:
            self._purge_locked()
            return list(self._snaps)

    def _purge_locked(self) -> None:
        now = _utc_now()
        expired = [sid for sid, s in self._snaps.items() if s.expired(now)]
        for sid in expired:
            del self._snaps[sid]

    def _evict_locked(self) -> None:
        """上限驱逐:份数 > 3 或总量 > 64MiB 时,从最旧的未冻结快照开始移除。"""
        unpinned = [s for s in self._snaps.values() if not s.frozen]
        unpinned.sort(key=lambda s: s.captured_at)
        total = sum(len(s.png) for s in self._snaps.values())
        while unpinned and (
            len(self._snaps) > MAX_LIVE_SNAPSHOTS or total > MAX_TOTAL_BYTES
        ):
            victim = unpinned.pop(0)
            total -= len(victim.png)
            del self._snaps[victim.id]
