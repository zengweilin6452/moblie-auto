"""驱动截图双通道策略单元测试(工单 03):注入 FakeChannel,不触碰 adb。"""

import io

import pytest
from fakes import FakeBackend, make_session
from PIL import Image

from mobile_auto import MobileAdapter, MobileConfig
from mobile_auto.contracts import Locator, Target
from mobile_auto.errors import StreamUnavailable
from mobile_auto.imaging.store import TemplateStore


class FakeChannel:
    def __init__(self, serial, *, fail=False, healthy=True, payload=b"scrcpy-png"):
        self.serial = serial
        self.fail = fail
        self._healthy = healthy
        self.payload = payload
        self.start_calls = 0
        self.stop_calls = 0
        self.captures = 0

    def start(self):
        self.start_calls += 1

    def healthy(self):
        return self._healthy

    def capture_png(self):
        if self.fail:
            raise StreamUnavailable("通道故障")
        self.captures += 1
        return self.payload

    def stop(self):
        self.stop_calls += 1
        self._healthy = False


def session_with_channel(factory, source="auto"):
    session, backend = make_session(
        MobileConfig(device_serial="SERIAL1", screenshot_source=source),
        backend=FakeBackend(),
    )
    session._channel_factory = factory
    session.start()
    return session, backend


# ── 配置 ─────────────────────────────────────────────────


def test_config_default_auto():
    assert MobileConfig().screenshot_source == "auto"


def test_config_rejects_unknown_source():
    with pytest.raises(Exception, match="screenshot_source"):
        MobileConfig(screenshot_source="h264")


# ── 策略分支 ──────────────────────────────────────────────


def test_u2_forced_never_touches_channel():
    channel = FakeChannel("S")
    session, backend = session_with_channel(lambda s: channel, source="u2")
    try:
        result = session.screenshot_bytes(backend)
        assert result != b"scrcpy-png"  # 来自 u2 后端
        assert backend.screenshot_calls == 1
        assert channel.start_calls == 0 and channel.captures == 0
    finally:
        session.close()


def test_auto_uses_scrcpy_and_resets_counters():
    channel = FakeChannel("S")
    session, backend = session_with_channel(lambda s: channel, source="auto")
    try:
        assert session.screenshot_bytes(backend) == b"scrcpy-png"
        assert channel.captures == 1
        assert backend.screenshot_calls == 0
        assert session._scrcpy_failures == 0
    finally:
        session.close()


def test_auto_falls_back_and_degrades_after_three_failures():
    channel = FakeChannel("S", fail=True)
    session, backend = session_with_channel(lambda s: channel, source="auto")
    try:
        for _ in range(3):
            result = session.screenshot_bytes(backend)
            assert result != b"scrcpy-png"  # 回退 u2
        assert session._scrcpy_degraded_until > 0
        attempts = channel.start_calls
        session.screenshot_bytes(backend)  # 降级窗口内不再尝试通道
        assert channel.start_calls == attempts
    finally:
        session.close()


def test_auto_recovers_after_degrade_window():
    channel = FakeChannel("S", fail=True)
    session, backend = session_with_channel(lambda s: channel, source="auto")
    try:
        for _ in range(3):
            session.screenshot_bytes(backend)
        session._scrcpy_degraded_until = 0.0  # 模拟降级窗口到期
        channel.fail = False
        assert session.screenshot_bytes(backend) == b"scrcpy-png"
        assert session._scrcpy_failures == 0
        assert session._scrcpy_degraded_until == 0.0
    finally:
        session.close()


def test_scrcpy_forced_raises_without_silent_fallback():
    channel = FakeChannel("S", fail=True)
    session, backend = session_with_channel(lambda s: channel, source="scrcpy")
    try:
        with pytest.raises(StreamUnavailable):
            session.screenshot_bytes(backend)
    finally:
        session.close()


def test_unhealthy_channel_recreated():
    unhealthy = FakeChannel("S", healthy=False)
    healthy = FakeChannel("S")
    channels = [unhealthy, healthy]
    session, _ = session_with_channel(lambda s: channels.pop(0), source="auto")
    try:
        session.get_or_start_channel()  # 首次:创建不健康通道
        session.get_or_start_channel()  # 再次:回收并重建
        assert unhealthy.stop_calls == 1
        assert session._scrcpy_channel is healthy
    finally:
        session.close()


def test_close_stops_channel():
    channel = FakeChannel("S")
    session, backend = session_with_channel(lambda s: channel, source="auto")
    session.screenshot_bytes(backend)
    session.close()
    assert channel.stop_calls == 1


# ── 适配器集成 ────────────────────────────────────────────


async def test_adapter_screenshot_routes_through_policy():
    channel = FakeChannel("S")
    session, backend = session_with_channel(lambda s: channel, source="auto")
    adapter = MobileAdapter(session)
    try:
        assert await adapter.screenshot() == b"scrcpy-png"
    finally:
        session.close()


async def test_image_match_uses_policy_too(tmp_path):
    """IMAGE 重匹配取图走同一策略(工单 03 范围)。"""
    channel = FakeChannel("S", payload=_device_png(1080, 2400))
    session, backend = make_session(
        MobileConfig(
            device_serial="SERIAL1",
            screenshot_source="auto",
            assets_dir=tmp_path / "assets",
        ),
        backend=FakeBackend(),
    )
    session._channel_factory = lambda serial: channel
    session.start()
    adapter = MobileAdapter(session)
    try:
        TemplateStore(session.config.assets_dir).create(
            "probe/target.png",
            _solid_png(40, 40),
            source_width=1080,
            source_height=2400,
            note="unit",
        )
        handles = await adapter.find_elements(
            Target(Locator.IMAGE, "probe/target.png", confidence=0.1)
        )
        assert isinstance(handles, list)
        assert channel.captures >= 1  # 采集走的是 scrcpy 通道
    finally:
        session.close()


def _solid_png(width: int, height: int) -> bytes:
    """有纹理的测试模板(纯色会被低纹理守卫拒绝)。"""
    import numpy as np

    rng = np.random.default_rng(7)
    array = rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)
    image = Image.fromarray(array)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _device_png(width: int, height: int) -> bytes:
    """可解码的整屏 PNG(供 IMAGE 匹配走通采集→解码全链路)。"""
    return _solid_png(width, height)
