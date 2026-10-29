"""预览视频流化单元测试(工单 02):注入 FakeChannel,验证来源切换/回退/暂停。"""

import time

from conftest import ViewerBackend, make_viewer_session

from mobile_auto.errors import StreamUnavailable
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore

FAKE_JPEG = b"fake-jpeg-bytes"


class FakeChannel:
    def __init__(self, serial, *, fail=False):
        self.serial = serial
        self.fail = fail
        self.started = False
        self.stop_calls = 0

    def start(self):
        self.started = True

    def healthy(self):
        return self.started and not self.fail

    def screen_size(self):
        return 1080, 2400

    def latest_frame(self):
        if self.fail:
            raise StreamUnavailable("通道损坏")
        return FAKE_JPEG, 150, 200

    def stop(self):
        self.stop_calls += 1
        self.started = False


def make_app(channel, *, preview_interval=0.05, stream_interval=0.02, idle_stop=30.0):
    session, _ = make_viewer_session(ViewerBackend())
    manager = _ChannelManager(
        session,
        idle_stop=idle_stop,
        retry_init=0.05,
        channel_factory=lambda serial: channel,
    )
    app = create_app(
        session,
        SnapshotStore(),
        preview_interval=preview_interval,
        stream_interval=stream_interval,
        channel_manager=manager,
    )
    return app, session, manager


def test_stream_frames_prefer_scrcpy_channel():
    channel = FakeChannel("SERIAL1")
    app, _, manager = make_app(channel)
    from fastapi.testclient import TestClient

    with TestClient(app).websocket_connect("/api/v1/preview") as ws:
        frame = ws.receive_json()
    manager.shutdown()
    assert frame["kind"] == "frame"
    assert frame["source"] == "scrcpy"
    assert frame["width"] == 150 and frame["height"] == 200
    assert "decode_ms" in frame


def test_stream_falls_back_to_u2_when_channel_factory_fails():
    channel = FakeChannel("SERIAL1", fail=True)  # start 成功但通道 unhealthy?构造失败更直接:
    # factory 抛异常 → 回退 u2;用坏通道触发的失败也一样落回退,这里直接测 unhealthy 路径
    app, _, manager = make_app(channel)
    from fastapi.testclient import TestClient

    with TestClient(app).websocket_connect("/api/v1/preview") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    manager.shutdown()
    for frame in (first, second):
        assert frame["kind"] == "frame"
        assert frame["source"] == "u2-fallback"
        assert frame["fallback_reason"]


def test_stream_frames_continue_while_lease_held():
    """通道健康 + 租约被测试持有:scrcpy 读取不占租约,画面帧照常下发。"""
    session, _ = make_viewer_session(ViewerBackend())
    channel = FakeChannel("SERIAL1")
    manager = _ChannelManager(
        session, retry_init=0.05, channel_factory=lambda serial: channel
    )
    manager._channel = channel  # 预置已启动
    channel.started = True
    app = create_app(
        session, SnapshotStore(), preview_interval=0.05,
        stream_interval=0.02, channel_manager=manager,
    )
    from fastapi.testclient import TestClient

    session.acquire_lease()  # 他人持有租约(如 pytest 运行中)
    try:
        with TestClient(app).websocket_connect("/api/v1/preview") as ws:
            frame = ws.receive_json()
    finally:
        session.release_lease()
    manager.shutdown()
    assert frame["kind"] == "frame"
    assert frame["source"] == "scrcpy"


def test_channel_recycled_after_stream_error_then_recovers():
    """通道不健康 → 回收并回退;退避到期后重建新通道恢复视频流。"""
    channels = []
    state = {"fail": True}

    def factory(serial):
        ch = FakeChannel(serial, fail=state["fail"])
        channels.append(ch)
        return ch

    session, _ = make_viewer_session(ViewerBackend())
    manager = _ChannelManager(session, retry_init=0.05, channel_factory=factory)
    app = create_app(
        session, SnapshotStore(), preview_interval=0.05,
        stream_interval=0.02, channel_manager=manager,
    )
    from fastapi.testclient import TestClient

    with TestClient(app).websocket_connect("/api/v1/preview") as ws:
        ws.receive_json()  # 首帧:通道不健康 → 回收 → 本轮回退
        assert channels[0].stop_calls >= 1
        state["fail"] = False  # 退避到期后重建的通道恢复健康
        deadline = time.monotonic() + 5
        recovered = None
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if frame.get("source") == "scrcpy":
                recovered = frame
                break
    manager.shutdown()
    assert recovered is not None, "退避重试后未恢复视频流"
    assert len(channels) >= 2  # 旧通道回收,新通道重建


def test_idle_stop_after_last_client_disconnects():
    channel = FakeChannel("SERIAL1")
    app, _, manager = make_app(channel, idle_stop=0.2)
    from fastapi.testclient import TestClient

    with TestClient(app).websocket_connect("/api/v1/preview") as ws:
        ws.receive_json()
    time.sleep(0.5)  # 等待空闲自停计时
    assert channel.stop_calls == 1
    assert manager._channel is None


def test_two_clients_share_channel_and_idle_requires_both_gone():
    channel = FakeChannel("SERIAL1")
    app, _, manager = make_app(channel, idle_stop=0.2)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    with client.websocket_connect("/api/v1/preview") as ws1:
        ws1.receive_json()
        with client.websocket_connect("/api/v1/preview") as ws2:
            ws2.receive_json()
        time.sleep(0.4)  # 只剩一个客户端:不停止
        assert channel.stop_calls == 0
    time.sleep(0.4)  # 全部断开:停止
    assert channel.stop_calls == 1
    manager.shutdown()


class WarmingChannel(FakeChannel):
    """预热通道:server 已起、首个关键帧未到(has_frame=False 且取帧抛错)。"""

    def __init__(self, serial):
        super().__init__(serial)
        self.has_frame = False

    def latest_frame(self):
        if not self.has_frame:
            raise StreamUnavailable("视频流尚无解码帧")
        return FAKE_JPEG, 150, 200


def test_warmup_channel_not_recycled_and_recovers():
    """真机回归:静态画面下首帧未到时轮询不得回收通道(否则反复重建 server)。"""
    channel = WarmingChannel("SERIAL1")
    app, _, manager = make_app(channel)
    from fastapi.testclient import TestClient

    with TestClient(app).websocket_connect("/api/v1/preview") as ws:
        first = ws.receive_json()
        assert first["source"] == "u2-fallback"
        assert channel.stop_calls == 0  # 预热不回收通道
        channel.has_frame = True  # 首个关键帧到达
        deadline = time.monotonic() + 2
        source = None
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if frame.get("source") == "scrcpy":
                source = "scrcpy"
                break
        assert channel.stop_calls == 0  # 全程未回收(仅 shutdown 才停)
    manager.shutdown()
    assert source == "scrcpy", "预热结束后未切回 scrcpy 画面"
