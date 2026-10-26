"""坐标基准保鲜(工单 04):校验、节流、刷新、不可刷新通道与观测字段。

全部使用伪造通道/后端,不触碰真机;viewer 用例复用与
test_control_injection.py 相同的单设备装配方式。
"""

import json
import time

from conftest import NullLease
from fakes import FakeBackend, make_session
from fastapi.testclient import TestClient

from mobile_auto import DeviceSession, MobileAdapter, MobileConfig
from mobile_auto.evidence.collector import collect_failure_evidence
from mobile_auto.evidence.journal import Journal
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore


class ProbeBackend(FakeBackend):
    """记录 screen_size 探针次数(节流断言用)。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen_calls = 0

    def screen_size(self):
        self.screen_calls += 1
        return self.screen


class BasisChannel:
    """可刷新通道:refresh 时向后端重探尺寸。"""

    can_refresh_screen_size = True

    def __init__(self, backend, size=(1080, 2400)):
        self.backend = backend
        self.size = tuple(size)
        self.refresh_calls = 0
        self.started = False
        self.events = []
        self.taps = []
        self.swipes = []
        self.keys = []
        self.wakes = 0

    def start(self, timeout=0):
        self.started = True

    def stop(self):
        self.started = False

    def healthy(self):
        return self.started

    def screen_size(self):
        return self.size

    def refresh_screen_size(self):
        self.refresh_calls += 1
        self.size = tuple(self.backend.screen)

    def touch_down(self, x, y):
        self.events.append(("down", x, y))

    def touch_move(self, x, y):
        self.events.append(("move", x, y))

    def touch_up(self, x, y):
        self.events.append(("up", x, y))

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, x0, y0, x1, y1, duration_s=0.3):
        self.swipes.append((x0, y0, x1, y1, duration_s))

    def press_key(self, name):
        self.keys.append(name)

    def wake(self):
        self.wakes += 1


class ScaledChannel(BasisChannel):
    """不可刷新通道(模拟 upstream):尺寸可被视频缩放,刷新是空实现。"""

    can_refresh_screen_size = False

    def refresh_screen_size(self):
        self.refresh_calls += 1
        raise AssertionError("不可刷新通道不应被调用")


def make_env(
    *,
    channel_size=(1080, 2400),
    backend_screen=(1080, 2400),
    channel_cls=BasisChannel,
    artifacts_dir=None,
):
    overrides = {"device_serial": "SERIAL1"}
    if artifacts_dir is not None:
        overrides["artifacts_dir"] = artifacts_dir
    config = MobileConfig(**overrides)
    backend = ProbeBackend(screen=backend_screen)
    channel = channel_cls(backend, channel_size)
    session, _ = make_session(
        config=config, backend=backend, channel_factory=lambda serial: channel
    )
    session.start()
    session.release_lease()
    return MobileAdapter(session), session, channel, backend


def journal_lines(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── 04a 基准保鲜 ───────────────────────────────────────────────


async def test_size_change_refreshes_before_injection_once():
    adapter, _, channel, backend = make_env()
    backend.screen = (2400, 1080)  # 旋转 90°:后端实时尺寸已变
    assert await adapter.device.tap(1200, 540) == "scrcpy"
    assert channel.refresh_calls == 1
    assert channel.size == (2400, 1080)  # 注入前已重取基准
    assert channel.events == [("down", 1200, 540), ("up", 1200, 540)]
    assert backend.taps == []


async def test_throttle_probes_at_most_once_per_second():
    adapter, session, channel, backend = make_env()
    await adapter.device.tap(10, 20)
    await adapter.device.tap(30, 40)
    assert backend.screen_calls == 1  # 1s 内第二次不重复 wm size
    assert len(channel.events) == 4

    time.sleep(1.05)
    await adapter.device.tap(50, 60)
    assert backend.screen_calls == 2


async def test_consistent_size_keeps_existing_behavior():
    adapter, _, channel, backend = make_env()
    assert await adapter.device.tap(540, 1200) == "scrcpy"
    assert channel.refresh_calls == 0
    assert backend.screen_calls == 1
    assert channel.events == [("down", 540, 1200), ("up", 540, 1200)]


async def test_explicit_scrcpy_checks_but_never_reroutes():
    adapter, _, channel, backend = make_env()
    backend.screen = (2400, 1080)
    assert await adapter.device.tap(1200, 540, source="scrcpy") == "scrcpy"
    assert channel.refresh_calls == 1
    assert channel.events == [("down", 1200, 540), ("up", 1200, 540)]
    assert backend.taps == []  # 不改道 u2


async def test_source_u2_skips_basis_probe():
    adapter, _, channel, backend = make_env()
    assert await adapter.device.tap(1, 2, source="u2") == "u2"
    assert backend.screen_calls == 0
    assert channel.refresh_calls == 0
    assert backend.taps == [(1, 2)]


async def test_non_refreshable_scaled_basis_is_treated_as_same_display():
    """upstream 视频尺寸可缩放:宽高比一致视为同一显示区,不调用刷新。"""
    adapter, _, channel, _ = make_env(
        channel_size=(486, 1080), channel_cls=ScaledChannel
    )
    assert await adapter.device.tap(400, 900) == "scrcpy"
    assert channel.refresh_calls == 0
    assert channel.events == [("down", 400, 900), ("up", 400, 900)]


async def test_non_refreshable_rotation_is_recorded_not_faked(tmp_path):
    adapter, session, channel, backend = make_env(
        channel_size=(486, 1080), channel_cls=ScaledChannel, artifacts_dir=tmp_path
    )
    session.set_journal(Journal(tmp_path, session.session_id))
    channel.backend.screen = (2400, 1080)  # 旋转后比例翻转,视频元包尚未更新
    backend.screen = (2400, 1080)
    assert await adapter.device.tap(1200, 540) == "scrcpy"
    assert channel.refresh_calls == 0  # 空实现通道不被假装调用
    [line] = journal_lines(tmp_path / "actions.jsonl")
    basis = line["coordinate_basis"]
    assert basis["refreshable"] is False
    assert basis["refreshed"] is False
    assert basis["consistent"] is False
    assert basis["channel"] == [486, 1080]
    assert basis["backend"] == [2400, 1080]
    session.journal.close()


# ── 04b 可观测 ─────────────────────────────────────────────────


async def test_journal_injection_row_carries_coordinate_basis(tmp_path):
    adapter, session, channel, backend = make_env(artifacts_dir=tmp_path)
    session.set_journal(Journal(tmp_path, session.session_id))
    backend.screen = (2400, 1080)
    await adapter.device.tap(1200, 540)
    [line] = journal_lines(tmp_path / "actions.jsonl")
    basis = line["coordinate_basis"]
    assert basis["channel"] == [2400, 1080]
    assert basis["backend"] == [2400, 1080]
    assert basis["refreshed"] is True
    assert basis["checked_at"].endswith("Z")
    assert line["channel"] == "scrcpy"
    session.journal.close()


async def test_u2_injection_row_has_no_coordinate_basis(tmp_path):
    adapter, session, _, _ = make_env(artifacts_dir=tmp_path)
    session.set_journal(Journal(tmp_path, session.session_id))
    await adapter.device.tap(10, 20, source="u2")
    [line] = journal_lines(tmp_path / "actions.jsonl")
    assert "coordinate_basis" not in line
    session.journal.close()


# ── viewer 路径 + 旋转后命中 + 证据两组尺寸 ────────────────────


def viewer_env(tmp_path):
    backend = ProbeBackend(screen=(1080, 2400))
    channel = BasisChannel(backend)
    config = MobileConfig(
        device_serial="SERIAL1",
        artifacts_dir=tmp_path,
        assets_dir=tmp_path / "assets",
    )
    session = DeviceSession(
        config,
        backend_factory=lambda: backend,
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda serial: NullLease(serial),
    )
    session.start()
    session.release_lease()
    manager = _ChannelManager(
        session, retry_init=0.05, channel_factory=lambda serial: channel
    )
    app = create_app(session, SnapshotStore(), preview_interval=0.05, channel_manager=manager)
    return TestClient(app), session, channel, backend, manager


def test_viewer_rotated_injection_hits_expected_pixels_and_meta(tmp_path):
    http, session, channel, backend, manager = viewer_env(tmp_path)
    journal = Journal(tmp_path / "viewer" / "SERIAL1", session.session_id)
    session.set_journal(journal)
    try:
        http.post("/api/v1/control", json={"enabled": True})
        backend.screen = (2400, 1080)  # 设备旋转:通道基准仍旧
        assert http.post("/api/v1/input/touch", json={"nx": 0.5, "ny": 0.5}).status_code == 200
        assert channel.taps == [(1200, 540)]
        assert channel.refresh_calls == 1

        [line] = journal_lines(journal.path)
        basis = line["coordinate_basis"]
        assert basis["channel"] == [2400, 1080]
        assert basis["backend"] == [2400, 1080]
        assert basis["refreshed"] is True

        record = collect_failure_evidence(session, AssertionError("旋转后校验"))
        meta = json.loads(
            (tmp_path / "evidence" / record["evidence_id"] / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        spaces = meta["device"]["coordinate_spaces"]
        assert spaces["channel"] == [2400, 1080]
        assert spaces["backend"] == [2400, 1080]
    finally:
        journal.close()
        manager.shutdown()
        session.close()


def test_viewer_swipe_uses_refreshed_basis(tmp_path):
    http, session, channel, backend, manager = viewer_env(tmp_path)
    try:
        http.post("/api/v1/control", json={"enabled": True})
        backend.screen = (2400, 1080)
        res = http.post(
            "/api/v1/input/swipe",
            json={"nx0": 0.5, "ny0": 0.8, "nx1": 0.5, "ny1": 0.2, "duration_ms": 250},
        )
        assert res.status_code == 200
        assert channel.swipes == [(1200, 864, 1200, 216, 0.25)]
        assert channel.refresh_calls == 1
    finally:
        manager.shutdown()
        session.close()
