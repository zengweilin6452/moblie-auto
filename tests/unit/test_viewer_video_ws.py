"""浏览器直解视频 WS 单元测试(web-preview 02):严格同源、hello、编码包中继。"""

from contextlib import ExitStack

import pytest
from conftest import NullLease, ViewerBackend
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mobile_auto import DeviceSession, MobileConfig
from mobile_auto.stream.upstream import EncodedPacket, EncodedSubscriber
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore
from mobile_auto.viewer.videostream import (
    KIND_CONFIG,
    KIND_FRAME,
    KIND_SESSION,
    parse_relay_header,
)

ORIGIN = {"origin": "http://testserver"}


class FakeUpstreamChannel:
    channel_name = "upstream"

    def __init__(self, serial):
        self.serial = serial
        self.started = False
        self.stopped = False
        self.subscribers = []
        self.sub_capacity = 8  # 溢出用例改为 1

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def healthy(self):
        return self.started and not self.stopped

    @property
    def generation(self):
        return 7

    def screen_size(self):
        return 1080, 2400

    def latest_frame(self):
        raise NotImplementedError

    def subscribe_encoded(self):
        sub = EncodedSubscriber(
            max_packets=self.sub_capacity, max_bytes=1 << 20
        )
        self.subscribers.append(sub)
        return sub


@pytest.fixture
def video_env(tmp_path):
    channel = FakeUpstreamChannel("SERIAL1")
    config = MobileConfig(device_serial="SERIAL1", assets_dir=tmp_path / "assets")
    session = DeviceSession(
        config,
        backend_factory=lambda: ViewerBackend(),
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    session.release_lease()
    manager = _ChannelManager(
        session, retry_init=0.05, channel_factory=lambda serial: channel
    )
    app = create_app(
        session, SnapshotStore(), preview_interval=0.05, channel_manager=manager
    )
    yield TestClient(app), session, channel, manager
    manager.shutdown()
    session.close()


class StartupKeyframeChannel(FakeUpstreamChannel):
    """只在启动时产生关键帧,复现不定期发送 IDR 的设备编码器。"""

    def start(self):
        super().start()
        for sub in self.subscribers:
            sub.offer(EncodedPacket("config", b"SPSPPS", False, 0, 1080, 2400))
            sub.offer(EncodedPacket("session", b"", False, 0, 1080, 2400))
            sub.offer(EncodedPacket("frame", b"startup-IDR", True, 0, 1080, 2400))

    def stop(self):
        super().stop()
        for sub in self.subscribers:
            sub.close()


@pytest.mark.parametrize("existing_preview", [False, True])
def test_video_resubscribe_gets_startup_keyframe(video_env, existing_preview):
    _, _, _, manager = video_env
    channels = []

    def factory(serial):
        channel = StartupKeyframeChannel(serial)
        channels.append(channel)
        return channel

    manager._channel_factory = factory
    if existing_preview:
        assert manager.get() is channels[0]  # JPEG 已先启动,首个 IDR 已错过
    previous = channels[-1] if channels else None
    for _ in range(2):  # 首次订阅、刷新/重连均须能收到 IDR
        subscribers = []
        channel = manager.get_for_video(
            lambda ch, subscribers=subscribers: subscribers.append(ch.subscribe_encoded())
        )
        sub = subscribers[0]
        packets = [sub.take(0.05) for _ in range(3)]
        assert all(packet is not None for packet in packets), "新订阅未收到启动关键帧"
        assert [packet.kind for packet in packets] == ["config", "session", "frame"]
        assert packets[-1].key and packets[-1].payload == b"startup-IDR"
        assert channel.healthy()
        if previous is not None:
            assert channel is not previous
            assert previous.stopped
        previous = channel
        sub.close()


def test_second_video_subscriber_rebuilds_and_closes_old_subscription(video_env):
    _, _, _, manager = video_env
    manager._channel_factory = StartupKeyframeChannel
    first_subs = []
    first = manager.get_for_video(lambda ch: first_subs.append(ch.subscribe_encoded()))
    second_subs = []
    second = manager.get_for_video(lambda ch: second_subs.append(ch.subscribe_encoded()))
    assert second is not first
    assert first.stopped and first_subs[0].closed
    assert second.healthy() and not second_subs[0].closed
    assert second_subs[0].take(0.05).kind == "config"
    assert second_subs[0].take(0.05).kind == "session"
    assert second_subs[0].take(0.05).key


def test_video_ws_reconnect_relays_startup_keyframe(video_env):
    http, _, _, manager = video_env
    manager._channel_factory = StartupKeyframeChannel
    previous = manager.get()  # 已有 JPEG 通道,视频客户端属于晚到订阅者
    for _ in range(2):
        with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
            ws.send_json({"proto": 1})
            assert ws.receive_json()["kind"] == "ready"
            for kind in (KIND_CONFIG, KIND_SESSION, KIND_FRAME):
                message = ws.receive_bytes()
                header = parse_relay_header(message[:27])
                assert header["kind"] == kind
            assert message[27:] == b"startup-IDR"
            assert previous.stopped
            previous = manager.peek_healthy()


@pytest.mark.parametrize("rejected_path", ["/api/v1/preview", "/api/v1/preview/video"])
def test_rejected_preview_connection_keeps_active_client_counts(video_env, rejected_path):
    http, _, channel, manager = video_env
    channel.latest_frame = lambda: (b"JPEG", 1080, 2400)
    with ExitStack() as stack:
        # JPEG 与视频共享每设备四个槽位;视频连接停在 hello 前即可占槽。
        for path in ("/api/v1/preview", "/api/v1/preview"):
            ws = stack.enter_context(http.websocket_connect(path))
            assert ws.receive_json()["source"] == "scrcpy"
        for _ in range(2):
            stack.enter_context(http.websocket_connect("/api/v1/preview/video", headers=ORIGIN))
        assert manager.clients == http.app.state.ws_clients == 4
        for _ in range(2):  # 连续拒绝不能逐次偷减其他连接的名额
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with http.websocket_connect(
                    rejected_path, headers=ORIGIN if rejected_path.endswith("/video") else {}
                ):
                    pass
            assert exc_info.value.code == 4429
            assert manager.clients == http.app.state.ws_clients == 4
    assert manager.clients == http.app.state.ws_clients == 0

    # 活跃连接退出后名额确实可复用,并且正常关闭只释放自身一次。
    with http.websocket_connect("/api/v1/preview") as ws:
        assert ws.receive_json()["source"] == "scrcpy"
        assert manager.clients == http.app.state.ws_clients == 1
    assert manager.clients == http.app.state.ws_clients == 0


def test_video_bad_hello_releases_only_its_registered_slot(video_env):
    http, _, _, manager = video_env
    manager.client_connected()  # 其他已注册用户
    try:
        with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
            assert manager.clients == 2
            ws.send_json({"proto": 99})
            assert ws.receive_json()["code"] == "bad-hello"
        assert manager.clients == 1
        assert http.app.state.ws_clients == 0
    finally:
        manager.client_disconnected()


def test_video_ws_rejects_missing_origin(video_env):
    http, _, _, _, = video_env
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with http.websocket_connect("/api/v1/preview/video"):
            pass
    assert exc_info.value.code == 4403


def test_video_ws_rejects_bad_hello(video_env):
    http, _, _, _ = video_env
    with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
        ws.send_json({"proto": 99})
        assert ws.receive_json()["code"] == "bad-hello"


def test_video_ws_rejects_non_upstream_backend(tmp_path):
    channel = FakeUpstreamChannel("SERIAL1")
    config = MobileConfig(
        device_serial="SERIAL1",
        assets_dir=tmp_path / "assets",
        stream_backend="wetest",
    )
    session = DeviceSession(
        config,
        backend_factory=lambda: ViewerBackend(),
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    session.release_lease()
    manager = _ChannelManager(
        session, retry_init=0.05, channel_factory=lambda serial: channel
    )
    app = create_app(session, SnapshotStore(), channel_manager=manager)
    http = TestClient(app)
    try:
        with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
            ws.send_json({"proto": 1})
            assert ws.receive_json()["code"] == "unsupported"
    finally:
        manager.shutdown()
        session.close()


def test_video_ws_relays_config_session_frame(video_env):
    http, _, channel, _ = video_env
    with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
        ws.send_json({"proto": 1})
        ready = ws.receive_json()
        assert ready["kind"] == "ready"
        assert ready["generation"] == channel.generation
        sub = channel.subscribers[0]
        sub.offer(EncodedPacket("config", b"SPSPPS", False, 0, 0, 0))
        sub.offer(EncodedPacket("session", b"", False, 0, 1080, 2400))
        sub.offer(EncodedPacket("frame", b"AU", True, 1000, 1080, 2400))
        for kind, payload in (
            (KIND_CONFIG, b"SPSPPS"),
            (KIND_SESSION, b""),
            (KIND_FRAME, b"AU"),
        ):
            message = ws.receive_bytes()
            header = parse_relay_header(message[:27])
            assert header["kind"] == kind
            assert header["generation"] == channel.generation
            assert message[27:] == payload


def test_video_ws_reports_overflow_and_closes(video_env):
    http, _, channel, _ = video_env
    with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
        ws.send_json({"proto": 1})
        assert ws.receive_json()["kind"] == "ready"
        # 溢出判定本身已在 test_upstream_subscriber 覆盖;此处只验证
        # 中继循环对溢出标志的反应:置位后下一轮发状态 JSON 并关闭
        sub = channel.subscribers[0]
        with sub._cond:
            sub._overflowed = True
        error = ws.receive_json()
        assert error["kind"] == "error"
        assert error["code"] == "overflow"


def test_video_ws_reports_unhealthy_channel(video_env):
    http, _, channel, _ = video_env
    with http.websocket_connect("/api/v1/preview/video", headers=ORIGIN) as ws:
        ws.send_json({"proto": 1})
        assert ws.receive_json()["kind"] == "ready"
        channel.stopped = True
        error = ws.receive_json()
        assert error["code"] == "channel-unhealthy"


def test_capabilities_reports_h264_when_upstream(video_env):
    http, _, _, _ = video_env
    res = http.get("/api/v1/preview/capabilities")
    assert res.status_code == 200
    body = res.json()
    assert body["device"] == "SERIAL1"
    assert body["h264_ws"] is True
    assert body["reason"] is None


def test_capabilities_reports_reason_when_asset_missing(video_env, monkeypatch):
    import mobile_auto.stream.upstream as upstream_mod
    from mobile_auto.errors import StreamUnavailable

    def boom():
        raise StreamUnavailable("缺少上游 scrcpy server")

    monkeypatch.setattr(upstream_mod, "upstream_asset", boom)
    http, _, _, _ = video_env
    body = http.get("/api/v1/preview/capabilities").json()
    assert body["h264_ws"] is False
    assert "缺少上游 scrcpy server" in body["reason"]
