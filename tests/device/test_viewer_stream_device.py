"""预览视频流化真机 e2e(工单 02):帧率实测、杀 server 回退与恢复。

上游通道只在画面变化时出帧(编码器静态无包),帧率用例因此用 adb
滚动制造连续动态画面;首个关键帧到达前的预热期允许回退 u2,用例
等到 scrcpy 档出现后再计时/断言。
"""

import threading
import time

import pytest

from mobile_auto import DeviceSession
from mobile_auto.viewer.app import _ChannelManager, create_app
from mobile_auto.viewer.store import SnapshotStore

pytestmark = pytest.mark.device


@pytest.fixture
def stream_app(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    session.release_lease()
    manager = _ChannelManager(session)
    app = create_app(session, SnapshotStore(), channel_manager=manager)
    from fastapi.testclient import TestClient

    yield TestClient(app), session, manager
    manager.shutdown()
    session.close()


def _kill_server(serial: str, channel) -> None:
    """按后端定位设备端 server 进程并杀:上游 cmdline 带 scid,wetest 带 socket 前缀。"""
    import adbutils

    scid = getattr(channel, "scid", None)
    pattern = f"scid={scid:08x}" if scid is not None else channel._sock_prefix
    adbutils.adb.device(serial=serial).shell(f"pkill -f '{pattern}' || true")


def _scroll_until(stop: threading.Event, serial: str) -> None:
    """后台持续上滑/下滑,制造连续动态画面(静态画面编码器不出帧)。"""
    import adbutils

    device = adbutils.adb.device(serial=serial)
    down = True
    while not stop.is_set():
        y0, y1 = (0.75, 0.35) if down else (0.35, 0.75)
        device.shell(f"input swipe 540 {int(2400 * y0)} 540 {int(2400 * y1)} 150")
        down = not down
        stop.wait(0.05)


def _wait_scrcpy(ws, timeout: float = 20.0):
    """等首个 scrcpy 档画面(预热期允许 u2 回退)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = ws.receive_json()
        if frame.get("source") == "scrcpy":
            return frame
    return None


def test_stream_reaches_target_fps(stream_app, mobile_config):
    """验收:连续动态画面下视频流档 ≥8fps(10 秒采样,按帧内容去重)。"""
    client, _, _ = stream_app
    stop = threading.Event()
    scroller = threading.Thread(
        target=_scroll_until, args=(stop, mobile_config.device_serial), daemon=True
    )
    scroller.start()
    try:
        with client.websocket_connect("/api/v1/preview") as ws:
            assert _wait_scrcpy(ws) is not None, "20s 内未出现 scrcpy 档画面"
            started = time.monotonic()
            seen = set()
            while time.monotonic() - started < 10.0:
                frame = ws.receive_json()
                if frame.get("source") == "scrcpy":
                    seen.add(frame.get("jpeg_base64"))
    finally:
        stop.set()
        scroller.join(timeout=5)
    assert len(seen) >= 80, f"10 秒内仅 {len(seen)} 个不同画面,未达 8fps"


def test_video_resubscribe_receives_keyframe(stream_app):
    """JPEG 已起流后两次视频订阅都在 ready 后 2s 内取得真实关键帧。"""
    _, _, manager = stream_app
    previous = manager.get()
    assert previous is not None
    for _ in range(2):
        subscribers = []
        channel = manager.get_for_video(
            lambda ch, subscribers=subscribers: subscribers.append(ch.subscribe_encoded())
        )
        sub = subscribers[0]
        try:
            assert channel is not previous
            assert not previous.healthy()
            deadline = time.monotonic() + 2.0
            keyframe = None
            while time.monotonic() < deadline:
                packet = sub.take(max(0, deadline - time.monotonic()))
                if packet is not None and packet.kind == "frame" and packet.key:
                    keyframe = packet
                    break
            assert keyframe is not None, "视频重订阅 2s 内未收到关键帧"
            assert keyframe.payload and keyframe.width > 0 and keyframe.height > 0
        finally:
            sub.close()
        previous = channel


def test_kill_server_falls_back_and_recovers(stream_app, mobile_config):
    """杀 server ≤3s 内回退 u2 档;退避重试后自动恢复视频流。"""
    client, _, manager = stream_app
    with client.websocket_connect("/api/v1/preview") as ws:
        assert _wait_scrcpy(ws) is not None, "20s 内未出现 scrcpy 档画面"
        channel = manager._channel
        _kill_server(mobile_config.device_serial, channel)
        deadline = time.monotonic() + 3.0
        fallback = None
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if frame.get("source") == "u2-fallback" or frame.get("kind") == "status":
                fallback = frame
                break
        assert fallback is not None, "杀 server 后 3s 内未回退"

        deadline = time.monotonic() + 20.0  # 退避(2s 起)+ 重建 server
        recovered = None
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if frame.get("source") == "scrcpy":
                recovered = frame
                break
    assert recovered is not None, "杀 server 后未自动恢复视频流"
