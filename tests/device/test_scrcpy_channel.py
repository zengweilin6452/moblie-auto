"""scrcpy 通道真机 spike(工单 01):server 生命周期、CAPTURE、视频流。

只做只读验证(启动/截图/取帧/停止),不注入触摸——注入在工单 04 验证。
"""

import time

import pytest

from mobile_auto.errors import StreamUnavailable
from mobile_auto.scrcpy_channel import ScrcpyChannel

pytestmark = pytest.mark.device


@pytest.fixture
def channel(mobile_config):
    ch = ScrcpyChannel(mobile_config.device_serial)
    ch.start(timeout=20)
    yield ch
    ch.stop()


def test_server_start_and_screen_info(channel):
    assert channel.started
    width, height = channel.screen_size()
    assert width > 0 and height > 0


def test_capture_png_native_resolution(channel, mobile_config):
    import cv2
    import numpy as np

    png = channel.capture_png()
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    width, height = channel.screen_size()
    assert img.shape[:2] == (height, width)  # CAPTURE 保持原始分辨率


def test_video_stream_frames_arrive(channel):
    import cv2
    import numpy as np

    started = time.monotonic()
    jpeg, width, height = channel.latest_frame()
    assert time.monotonic() - started < 5.0  # 首帧有界
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape[:2] == (height, width)


def test_capture_latency_recorded(channel):
    """时延观测(验收记录用):10 次取均值,只断言量级不承诺固定值。"""
    channel.capture_png()  # 首次含懒加载,不计入
    t0 = time.monotonic()
    for _ in range(10):
        channel.capture_png()
    avg_ms = (time.monotonic() - t0) * 100
    assert avg_ms < 1000, f"CAPTURE 平均 {avg_ms:.0f}ms,异常偏慢"


def test_stop_kills_server_and_restart(mobile_config):
    import adbutils

    channel = ScrcpyChannel(mobile_config.device_serial)
    channel.start(timeout=20)
    prefix = channel._sock_prefix
    channel.stop()
    dev = adbutils.adb.device(serial=mobile_config.device_serial)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        out = dev.shell(f"ps -A | grep {prefix} || true")
        if prefix not in out:
            break
        time.sleep(0.2)
    assert prefix not in dev.shell(f"ps -A | grep {prefix} || true"), "server 进程残留"

    again = ScrcpyChannel(mobile_config.device_serial)
    again.start(timeout=20)  # 可重新启动(新实例新前缀)
    assert again.started
    assert again.healthy()
    again.stop()


def test_capture_after_stream_unavailable_recycled(mobile_config):
    """通道未启动时操作明确报 StreamUnavailable,不触碰设备。"""
    channel = ScrcpyChannel(mobile_config.device_serial)
    with pytest.raises(StreamUnavailable):
        channel.capture_png()
    assert not channel.started
