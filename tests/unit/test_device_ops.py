"""adapter.device 手势扩展面:通道选择、整操作回退、UP 收尾与参数校验。

FakeChannel/FakeBackend 全部伪造,不触碰设备;auto 的"通道不可用即时
回退 u2"与 screenshot 双通道语义一致(scrcpy 首写失败同样整体回退)。
"""

import pytest
from fakes import FakeBackend, FakeChannel, make_session

from mobile_auto import ConfigurationError, InvalidTarget, MobileAdapter, MobileConfig
from mobile_auto.errors import StreamUnavailable


def make_adapter(channel=None, fail_start=False, injection_source="auto"):
    backend = FakeBackend()

    def factory(serial):
        if fail_start:
            raise StreamUnavailable("通道启动失败")
        return channel

    config = MobileConfig(device_serial="SERIAL1", injection_source=injection_source)
    session, _ = make_session(config=config, backend=backend, channel_factory=factory)
    session.start()
    session.release_lease()
    return MobileAdapter(session), backend


@pytest.fixture
def channel():
    ch = FakeChannel()
    ch.start()
    return ch


# ── auto:scrcpy 优先,回退 u2 ──────────────────────────────────


async def test_auto_tap_uses_scrcpy(channel):
    adapter, backend = make_adapter(channel=channel)
    assert await adapter.device.tap(540, 1000) == "scrcpy"
    assert channel.events == [("down", 540, 1000), ("up", 540, 1000)]
    assert backend.taps == []


async def test_auto_falls_back_when_channel_unavailable():
    adapter, backend = make_adapter(fail_start=True)
    assert await adapter.device.tap(540, 1000) == "u2"
    assert backend.taps == [(540, 1000)]


async def test_auto_swipe_first_write_failure_falls_back_whole_op(channel):
    channel.fail_on = {"down": StreamUnavailable("down 失败")}
    adapter, backend = make_adapter(channel=channel)
    assert await adapter.device.swipe(100, 100, 400, 100, 0.05) == "u2"
    assert channel.events == []  # DOWN 未发出:无任何残留写
    assert backend.swipes == [(100, 100, 400, 100, 0.05)]


async def test_auto_long_press_falls_back_to_u2_long_click(channel):
    channel.fail_on = {"down": StreamUnavailable("down 失败")}
    adapter, backend = make_adapter(channel=channel)
    assert await adapter.device.long_press(540, 1200, hold_s=0.01) == "u2"
    assert backend.long_clicks == [(540, 1200, 0.01)]


# ── source 钉死 ───────────────────────────────────────────────


async def test_source_u2_forces_backend_even_with_channel(channel):
    adapter, backend = make_adapter(channel=channel)
    assert await adapter.device.tap(1, 2, source="u2") == "u2"
    assert backend.taps == [(1, 2)] and channel.events == []


async def test_source_scrcpy_raises_when_unavailable():
    adapter, backend = make_adapter(fail_start=True)
    with pytest.raises(StreamUnavailable):
        await adapter.device.tap(1, 2, source="scrcpy")
    assert backend.taps == []


async def test_source_scrcpy_first_write_failure_raises_original(channel):
    original = StreamUnavailable("down 失败")
    channel.fail_on = {"down": original}
    adapter, backend = make_adapter(channel=channel)
    with pytest.raises(StreamUnavailable, match="down 失败"):
        await adapter.device.tap(1, 2, source="scrcpy")
    assert backend.taps == []  # 不静默换通道


# ── 手势安全:DOWN 后失败 → UP 收尾,不重放 ─────────────────────


async def test_tap_up_failure_recovers_via_backend_and_raises(channel):
    channel.fail_on = {"up": StreamUnavailable("up 失败")}
    adapter, backend = make_adapter(channel=channel)
    with pytest.raises(StreamUnavailable, match="up 失败"):
        await adapter.device.tap(540, 900)
    assert backend.touch_ups == [(540, 900)]  # u2 通道补发 UP
    assert backend.taps == []  # 不重放整个手势


async def test_swipe_mid_gesture_failure_recovers_at_last_point(channel):
    channel.fail_on = {"move": StreamUnavailable("move 失败")}
    adapter, backend = make_adapter(channel=channel)
    with pytest.raises(StreamUnavailable, match="move 失败"):
        await adapter.device.swipe(100, 100, 500, 100, 0.05)
    # 收尾坐标 = 首个 MOVE 目标(跨度 400px/5px=80 步,第一步 x=105)
    assert backend.touch_ups == [(105, 100)]
    assert backend.swipes == []  # DOWN 已发出:不整体重放


async def test_long_press_up_failure_recovers(channel):
    channel.fail_on = {"up": StreamUnavailable("up 失败")}
    adapter, backend = make_adapter(channel=channel)
    with pytest.raises(StreamUnavailable):
        await adapter.device.long_press(300, 400, hold_s=0.01)
    assert backend.touch_ups == [(300, 400)]
    assert backend.long_clicks == []


async def test_swipe_gesture_sequence_shape(channel):
    adapter, backend = make_adapter(channel=channel)
    assert await adapter.device.swipe(0, 0, 30, 0, 0.03) == "scrcpy"
    downs = [e for e in channel.events if e[0] == "down"]
    ups = [e for e in channel.events if e[0] == "up"]
    moves = [e for e in channel.events if e[0] == "move"]
    assert downs == [("down", 0, 0)] and ups == [("up", 30, 0)]
    assert len(moves) == 6 and moves[-1] == ("move", 30, 0)  # 30px/5px 步长
    assert backend.swipes == []


# ── 参数校验 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda d: d.tap(1.5, 2),
        lambda d: d.tap(True, 2),
        lambda d: d.swipe(0, 0, "30", 0, 0.1),
        lambda d: d.long_press(1, 2, hold_s=0),
        lambda d: d.long_press(1, 2, hold_s=11),
        lambda d: d.swipe(0, 0, 10, 0, duration_s=-1),
    ],
)
async def test_invalid_arguments_rejected(call, channel):
    adapter, _ = make_adapter(channel=channel)
    with pytest.raises(InvalidTarget):
        await call(adapter.device)


async def test_invalid_source_rejected(channel):
    adapter, _ = make_adapter(channel=channel)
    with pytest.raises(InvalidTarget, match="source"):
        await adapter.device.tap(1, 2, source="grpc")


def test_invalid_injection_source_config_rejected():
    with pytest.raises(ConfigurationError, match="injection_source"):
        MobileConfig(device_serial="S", injection_source="grpc")


# ── 契约路径透明替换(scroll/图像点击发送/press_key)──────────────


async def test_contract_scroll_uses_scrcpy_channel(channel):
    """契约 scroll 的手势注入透明走通道(scrcpy 优先),backend 不再被调用。"""
    adapter, backend = make_adapter(channel=channel)
    await adapter.scroll("down", 900)
    kinds = {e[0] for e in channel.events}
    assert {"down", "move", "up"} <= kinds
    assert backend.swipes == []


async def test_contract_scroll_first_write_failure_falls_back(channel):
    channel.fail_on = {"down": StreamUnavailable("down 失败")}
    adapter, backend = make_adapter(channel=channel)
    await adapter.scroll("down", 900)
    assert len(backend.swipes) == 1  # DOWN 未发出:整体回退 u2
    assert channel.events == []


async def test_config_injection_source_u2_pins_backend(channel):
    """配置 injection_source="u2" 钉死契约路径:通道在场也不使用。"""
    adapter, backend = make_adapter(channel=channel, injection_source="u2")
    await adapter.scroll("down", 900)
    assert len(backend.swipes) == 1 and channel.events == []


async def test_session_inject_tap_prefers_channel(channel):
    """图像点击事务的发送路径(session.inject_tap)走通道。"""
    adapter, backend = make_adapter(channel=channel)
    session = adapter._session
    session.submit(lambda b: session.inject_tap(b, 10, 20)).result(5)
    assert channel.events == [("down", 10, 20), ("up", 10, 20)]
    assert backend.taps == []


async def test_press_key_back_home_via_channel_other_keys_u2(channel):
    """back/home 双通道;其余键(enter)直接 u2,通道不参与。"""
    adapter, backend = make_adapter(channel=channel)
    await adapter.press_key("home")
    await adapter.press_key("enter")
    assert ("press", "home", 0) in channel.events
    assert backend.press_keys == ["enter"]


# ── 降级窗口(注入与截图共用同一通道健康状态)────────────────────


async def test_repeated_failures_open_degrade_window(channel):
    channel.fail_on = {"down": StreamUnavailable("down 失败")}
    adapter, backend = make_adapter(channel=channel)
    for _ in range(3):  # 达到 _SCRCPY_FAIL_LIMIT:窗口开启
        assert await adapter.device.tap(1, 2) == "u2"
    assert backend.taps == [(1, 2)] * 3

    channel.fail_on = {}  # 通道"恢复":窗口期内仍直接 u2,不触碰通道
    assert await adapter.device.tap(3, 4) == "u2"
    assert channel.events == []

    adapter._session._scrcpy_degraded_until = 0.0  # 模拟窗口到期再探测
    assert await adapter.device.tap(5, 6) == "scrcpy"
    assert ("down", 5, 6) in channel.events
