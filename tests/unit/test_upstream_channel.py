"""UpstreamChannel 单元测试(SU-01):假 socket 喂线格式字节流,不碰设备。"""

import socket
import struct
import threading
import time

import pytest

pytest.importorskip("av", reason="PyAV 未安装")

from mobile_auto.errors import StreamUnavailable  # noqa: E402
from mobile_auto.stream import upstream_proto as proto  # noqa: E402
from mobile_auto.stream.upstream import UpstreamChannel  # noqa: E402

# ── 线格式构造器(与 server 行为一致;供其他测试复用)──────


def video_header() -> bytes:
    return struct.pack(">I", 0x68323634)  # "h264"


def session_meta(w: int, h: int) -> bytes:
    return struct.pack(">iII", -(1 << 31), w, h)


def frame_packet(payload: bytes, *, pts: int = 100, key: bool = False) -> bytes:
    flags = pts | ((1 << 61) if key else 0)
    return struct.pack(">qi", flags, len(payload)) + payload


def config_packet(payload: bytes) -> bytes:
    return struct.pack(">qi", 1 << 62, len(payload)) + payload


class FakeAdbConnection:
    """模拟 adbutils create_connection 返回的双工连接(内存 socket 对)。"""

    def __init__(self, server_side: socket.socket):
        self._sock = server_side
        self.sent = b""

    def recv(self, n: int) -> bytes:
        return self._sock.recv(n)

    def send(self, data: bytes) -> int:
        # 真实发送(断链/关闭才能如实抛错),同时留副本供断言
        sent = self._sock.send(data)
        self.sent += data
        return sent

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class FakeAdbDevice:
    """shell(stream) 返回假 server 流;create_connection 出内存 socket 对。"""

    def __init__(self):
        self.pushes = []
        self.stream_closed = False
        self.connections = []
        self._pending: list[socket.socket] = []

    def shell(self, cmd, stream=False):
        if stream:
            return _FakeStream(self)
        return ""

    def sync_push(self, src, dst):
        self.pushes.append(dst)

    def create_connection(self, network, addr):
        client, server = socket.socketpair()
        conn = FakeAdbConnection(client)
        self.connections.append(conn)
        self._pending.append(server)
        if len(self.connections) == 1:
            server.sendall(b"\x00")  # server 行为:首个(video)连接发 dummy byte
        return conn

    def server_sockets(self):
        return list(self._pending)


class _FakeStream:
    def __init__(self, device: FakeAdbDevice):
        self._device = device

    def close(self):
        self._device.stream_closed = True


@pytest.fixture
def fake_adb(monkeypatch):
    import adbutils

    device = FakeAdbDevice()
    monkeypatch.setattr(adbutils.adb, "device", lambda serial: device)

    class _Sync:
        def push(self, src, dst):
            device.sync_push(src, dst)

    device.sync = _Sync()
    return device


def make_started_channel(
    fake_adb, *, meta: tuple[int, int] | None = (1080, 2400)
) -> tuple[UpstreamChannel, FakeAdbDevice]:
    """后台 start,video 连接建立后喂 codec 头(+可选会话元包),等 start 完成。

    全部喂字节经 feed_video(内建连接等待),时序确定、无跨测试泄漏。
    meta=None 时不等 start 完成(调用方续喂帧数据后再 join)。
    """
    channel = UpstreamChannel("SERIAL", scid=proto.encode_scid(12345, 1))
    t = threading.Thread(target=channel.start, kwargs={"timeout": 5.0})
    t.start()
    deadline = time.monotonic() + 2
    while len(fake_adb.connections) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    feed_video(channel, fake_adb, video_header())
    if meta is not None:
        feed_video(channel, fake_adb, session_meta(*meta))
        t.join(timeout=5)
        assert not t.is_alive(), "start 未在期限内完成"
    else:
        channel._start_thread = t
    return channel, fake_adb


def feed_video(channel: UpstreamChannel, fake_adb, data: bytes) -> None:
    """向 video socket 的 server 侧写数据(经 socketpair);连接未建立则等待。"""
    deadline = time.monotonic() + 5
    while not fake_adb.server_sockets() and time.monotonic() < deadline:
        time.sleep(0.01)
    server_sock = fake_adb.server_sockets()[0]
    server_sock.sendall(data)


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# ── 启动与握手 ────────────────────────────────────────────


def test_start_pushes_assets_and_starts_server(fake_adb):
    channel, _ = make_started_channel(fake_adb)
    try:
        assert any("scrcpy-server-upstream.jar" in p for p in fake_adb.pushes)
        assert channel.started
        assert channel._sock_prefix.startswith("scrcpy_")
    finally:
        channel.stop()


def test_start_waits_for_session_meta(fake_adb):
    """start 完成 = 会话元包已到(分辨率就绪);未喂时超时报错。"""
    channel = UpstreamChannel("SERIAL")
    with pytest.raises(StreamUnavailable, match="会话元包"):
        channel.start(timeout=0.3)  # video/control 已连但无元包
    channel.stop()


def test_start_idempotent(fake_adb):
    channel, _ = make_started_channel(fake_adb)
    try:
        pushes = len(fake_adb.pushes)
        channel.start()
        assert len(fake_adb.pushes) == pushes
    finally:
        channel.stop()


def test_socket_name_and_scid_ownership(fake_adb):
    channel = UpstreamChannel("SERIAL")
    host_mod = channel._host_mod
    assert channel.scid >> 8 == host_mod
    assert channel._sock_prefix == f"scrcpy_{channel.scid:08x}"


def test_video_header_and_session_meta_set_screen(fake_adb):
    channel, adb = make_started_channel(fake_adb)
    try:
        assert channel.screen_size() == (1080, 2400)  # start 内已喂元包
        feed_video(channel, adb, session_meta(496, 1080))  # 流内重发(裸 12B)
        assert wait_for(lambda: channel.screen_size() == (496, 1080))
    finally:
        channel.stop()


def test_wrong_video_header_kills_channel(fake_adb):
    """首包 codec 头非 h264 → 泵报错、通道降级(直接喂坏头,不经 helper)。"""
    import threading

    channel = UpstreamChannel("SERIAL", scid=proto.encode_scid(12345, 9))
    t = threading.Thread(target=channel.start, kwargs={"timeout": 3.0})
    t.start()
    deadline = time.monotonic() + 2
    while not fake_adb.server_sockets() and time.monotonic() < deadline:
        time.sleep(0.01)
    bad = bytes([0, 0, 0, 1])
    feed_video(channel, fake_adb, bad + session_meta(1, 1))
    t.join(timeout=5)
    assert channel._decode_error is not None
    assert not channel.healthy()
    channel.stop()

def _h264_keyframe_stream(channel, fake_adb):
    """喂 x264 编码的 64x64 帧序列(SPS/PPS 内联首关键帧;2x2 解不出帧,实测)。"""
    from fractions import Fraction

    import av

    codec = av.CodecContext.create("libx264", "w")
    codec.width = 64
    codec.height = 64
    codec.pix_fmt = "yuv420p"
    codec.time_base = Fraction(1, 25)
    codec.open()
    for i in range(4):
        frame = av.VideoFrame(64, 64, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts = i
        for pkt in codec.encode(frame):
            feed_video(
                channel,
                fake_adb,
                frame_packet(bytes(pkt), pts=1000 + i, key=pkt.is_keyframe),
            )
    for pkt in codec.encode(None):
        feed_video(channel, fake_adb, frame_packet(bytes(pkt), pts=9000))


def test_latest_frame_after_decode(fake_adb):
    pytest.importorskip("cv2")
    channel, adb = make_started_channel(fake_adb, meta=None)
    try:
        feed_video(channel, adb, session_meta(64, 64))
        _h264_keyframe_stream(channel, fake_adb)
        channel._start_thread.join(timeout=5)
        assert wait_for(lambda: channel._latest_jpeg is not None), (
            f"未出帧;decode_error={channel._decode_error}"
        )
        jpeg, w, h = channel.latest_frame()
        assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI
        assert (w, h) == (64, 64)
        assert channel.healthy()
    finally:
        channel.stop()


def test_capture_png_from_decoded_frame(fake_adb):
    pytest.importorskip("cv2")
    channel, adb = make_started_channel(fake_adb, meta=None)
    try:
        feed_video(channel, adb, session_meta(64, 64))
        _h264_keyframe_stream(channel, fake_adb)
        channel._start_thread.join(timeout=5)
        assert wait_for(lambda: channel._latest_jpeg is not None)
        png = channel.capture_png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        channel.stop()


def test_no_frame_raises_unavailable(fake_adb):
    channel, adb = make_started_channel(fake_adb, meta=(10, 20))
    try:
        with pytest.raises(StreamUnavailable):
            channel.latest_frame()
    finally:
        channel.stop()


def test_eof_marks_channel_unhealthy(fake_adb):
    channel, adb = make_started_channel(fake_adb, meta=(10, 20))
    try:
        fake_adb.server_sockets()[0].close()  # server 侧断开 → EOF
        assert wait_for(lambda: not channel.healthy())
    finally:
        channel.stop()


# ── 控制注入:逐字节断言 ─────────────────────────────────


def test_tap_sends_down_then_up_bytes(fake_adb):
    channel, adb = make_started_channel(fake_adb)
    try:
        assert channel.screen_size() == (1080, 2400)
        channel.tap(540, 1200)
        sent = adb.connections[1].sent  # 第二个连接 = control
        msg_len = 1 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4 + 4  # 32B
        down, up = sent[:msg_len], sent[msg_len:]
        assert len(sent) == 2 * msg_len
        assert down[0] == 2 and down[1] == 0  # type2 / ACTION_DOWN
        assert struct.unpack(">q", down[2:10])[0] == -1  # generic finger
        assert struct.unpack(">ii", down[10:18]) == (540, 1200)
        assert struct.unpack(">HH", down[18:22]) == (1080, 2400)
        assert struct.unpack(">H", down[22:24])[0] == 0xFFFF
        assert up[0] == 2 and up[1] == 2  # ACTION_UP
        assert down[2:] == up[2:]
    finally:
        channel.stop()


def test_press_key_back_sends_keycode_down_up(fake_adb):
    channel, adb = make_started_channel(fake_adb)
    try:
        assert channel.screen_size() == (1080, 2400)
        channel.press_key("back")
        sent = adb.connections[1].sent
        assert sent == struct.pack(">BBiii", 0, 0, 4, 0, 0) + struct.pack(
            ">BBiii", 0, 1, 4, 0, 0
        )
    finally:
        channel.stop()


def test_wake_sends_keycode_224(fake_adb):
    channel, adb = make_started_channel(fake_adb)
    try:
        assert channel.screen_size() == (1080, 2400)
        channel.wake()
        sent = adb.connections[1].sent
        assert struct.unpack(">i", sent[2:6])[0] == 224
        assert struct.unpack(">i", sent[16:20])[0] == 224
    finally:
        channel.stop()


def test_swipe_sends_down_moves_up(fake_adb):
    channel, adb = make_started_channel(fake_adb)
    try:
        assert channel.screen_size() == (1080, 2400)
        channel.swipe(100, 1200, 300, 1200, duration_s=0.05)
        sent = adb.connections[1].sent
        msgs = [sent[i : i + 32] for i in range(0, len(sent), 32)]
        assert msgs[0][1] == 0 and msgs[-1][1] == 2  # DOWN … UP
        assert all(m[0] == 2 for m in msgs)
        assert len(msgs) >= 4  # DOWN + ≥2 MOVE + UP
        last_x = struct.unpack(">i", msgs[-1][10:14])[0]
        assert last_x == 300
    finally:
        channel.stop()


def test_control_send_failure_raises_unavailable(fake_adb):
    channel, adb = make_started_channel(fake_adb)
    try:
        assert channel.screen_size() == (1080, 2400)
        channel._control_sock._sock.shutdown(socket.SHUT_RDWR)
        channel._control_sock.close()
        with pytest.raises(StreamUnavailable, match="控制消息"):
            channel.tap(1, 1)
    finally:
        channel.stop()


def test_stop_closes_everything(fake_adb):
    channel, adb = make_started_channel(fake_adb, meta=(10, 20))
    channel.stop()
    assert not channel.started
    assert channel._video_sock is None and channel._control_sock is None
    assert adb.stream_closed
    channel.stop()  # 幂等
