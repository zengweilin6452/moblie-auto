"""预览视频流化真机 e2e(工单 02):帧率实测、杀 server 回退与恢复。"""

import subprocess
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


def _kill_server(serial: str, prefix: str) -> None:
    import adbutils

    adbutils.adb.device(serial=serial).shell(f"pkill -f {prefix} || true")


def test_stream_reaches_target_fps(stream_app, mobile_config):
    """验收:视频流档 ≥8fps(10 秒采样)。"""
    client, _, _ = stream_app
    with client.websocket_connect("/api/v1/preview") as ws:
        deadline = time.monotonic() + 12
        frames = 0
        started = None
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            if frame.get("source") == "scrcpy":
                if started is None:
                    started = time.monotonic()
                frames += 1
                if time.monotonic() - started >= 10.0:
                    break
    assert frames >= 80, f"10 秒内仅 {frames} 帧,未达 8fps"


def test_kill_server_falls_back_and_recovers(stream_app, mobile_config):
    """杀 server ≤3s 内回退 u2 档;退避重试后自动恢复视频流。"""
    client, _, manager = stream_app
    with client.websocket_connect("/api/v1/preview") as ws:
        first = ws.receive_json()
        assert first.get("source") == "scrcpy"
        channel = manager._channel
        _kill_server(mobile_config.device_serial, channel._sock_prefix)
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
