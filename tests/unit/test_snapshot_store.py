"""快照库单元行为:上限驱逐、冻结固定引用与有效期。"""

from datetime import UTC, datetime, timedelta

from mobile_auto.viewer.store import (
    MAX_LIVE_SNAPSHOTS,
    Snapshot,
    SnapshotStore,
)


def make_snapshot(size_kb=1, captured_at=None, frozen=False):
    return Snapshot(
        png=b"x" * (size_kb * 1024),
        width=100,
        height=200,
        orientation="portrait",
        captured_at=captured_at or datetime.now(UTC).isoformat(),
        frozen=frozen,
        frozen_at=datetime.now(UTC) if frozen else None,
    )


def test_eviction_keeps_newest_three():
    store = SnapshotStore()
    ids = [store.add(make_snapshot()) for _ in range(5)]
    remaining = store.snapshot_ids()
    assert len(remaining) == MAX_LIVE_SNAPSHOTS
    assert ids[-3:] == remaining or set(ids[-3:]) <= set(remaining)  # 最旧的被驱逐


def test_frozen_snapshot_not_evicted():
    store = SnapshotStore()
    frozen_id = store.add(make_snapshot(frozen=True))
    for _ in range(5):
        store.add(make_snapshot())
    assert store.get(frozen_id) is not None  # 冻结固定引用,不受驱逐
    unfrozen = store.freeze(frozen_id, False)
    assert unfrozen is not None
    store.add(make_snapshot())
    assert store.get(frozen_id) is None  # 退出编辑后恢复驱逐


def test_expired_snapshots_invisible():
    store = SnapshotStore()
    old = datetime.now(UTC) - timedelta(seconds=121)
    sid = store.add(make_snapshot(captured_at=old.isoformat()))
    assert store.get(sid) is None


def test_freeze_unknown_returns_none():
    assert SnapshotStore().freeze("nope", True) is None
