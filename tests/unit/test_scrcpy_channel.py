"""scrcpy 通道单元测试(工单 01):伪造 wetest/adbutils 层,不碰设备。"""

from __future__ import annotations

import struct
import threading
from types import SimpleNamespace

import numpy as np
import pytest

wetest_scrcpy = pytest.importorskip("wetest.scrcpy", reason="stream extra 未安装")
Action = wetest_scrcpy.Action  # 枚举仅取值,无设备副作用

from mobile_auto.errors import StreamUnavailable  # noqa: E402
from mobile_auto.stream import channel as scrcpy_channel  # noqa: E402
from mobile_auto.stream.channel import ScrcpyChannel  # noqa: E402

SCREEN_W, SCREEN_H = 1080, 2400


class FakeSync:
    def __init__(self, pushes):
        self.pushes = pushes

    def push(self, src: str, dst: str) -> None:
        self.pushes.append((src, dst))


class FakeStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeAdbDevice:
    def __init__(self):
        self.shells: list[str] = []
        self.pushes: list[tuple[str, str]] = []
        self.sync = FakeSync(self.pushes)
        self.stream = FakeStream()

    def shell(self, cmd, stream=False):
        if stream:
            self.shells.append(" ".join(cmd))
            return self.stream
        self.shells.append(cmd)
        if cmd == "wm size":
            return f"Physical size: {SCREEN_W}x{SCREEN_H}\nOverride size: none\n"
        return ""


class FakeScreenInfo:
    Width = SCREEN_W
    Height = SCREEN_H


class FakeCtrl:
    instances: list[FakeCtrl] = []

    def __init__(self, device=None, timeout=5, *, fail_init=False):
        self.fail_init = fail_init
        if fail_init:
            raise ConnectionError("server 未就绪")
        import socket

        self._sock = socket.socket()  # healthy() 探测的底层 socket 形状
        self.sent: list[bytes] = []
        self.closed = False
        self.screen_info = FakeScreenInfo()
        FakeCtrl.instances.append(self)

    def write(self, data: bytes):
        self.sent.append(data)
        return len(data)

    def screenshot(self, max_size=1080, quality=80):
        arr = np.zeros((SCREEN_H, SCREEN_W, 3), dtype=np.uint8)
        arr[:60, :80] = 200
        return arr

    def get_rotation(self) -> int:
        return 0

    def get_screen_info(self) -> FakeScreenInfo:
        return FakeScreenInfo()

    def close(self):
        self.closed = True
        self._sock.close()


class FakeVideo:
    def __init__(self, device=None, timeout=5):
        self._thread = None
        self.closed = False
        self._release = threading.Event()

    def start(self):
        self._thread = threading.Thread(
            target=self._release.wait, daemon=True  # 阻塞保活,close 时放行
        )
        self._thread.start()

    def get_screenshot(self):
        arr = np.zeros((SCREEN_H // 5, SCREEN_W // 5, 3), dtype=np.uint8)
        arr[:10, :10] = 255
        return arr

    def close(self):
        self.closed = True
        self._release.set()
        self._thread = None


@pytest.fixture
def fakes(monkeypatch):
    adb_device = FakeAdbDevice()
    ctrl_cls_state = {"fail_init": False}

    def make_ctrl(device=None, timeout=5):
        return FakeCtrl(device, timeout, fail_init=ctrl_cls_state["fail_init"])

    import adbutils

    monkeypatch.setattr(adbutils.adb, "device", lambda serial: adb_device)
    monkeypatch.setattr("wetest.scrcpy.CtrlConnection", make_ctrl)
    monkeypatch.setattr("wetest.scrcpy.VideoStreamScreenshot", FakeVideo)
    monkeypatch.setattr("wetest.scrcpy.AndroidDevice", lambda **kw: object())
    return SimpleNamespace(adb=adb_device, ctrl_cls_state=ctrl_cls_state)


def started_channel(fakes) -> ScrcpyChannel:
    channel = ScrcpyChannel("SERIAL")
    channel.start(timeout=0.5)
    return channel


# ── 启动与资产 ─────────────────────────────────────────────


def test_start_pushes_assets_and_starts_server(fakes):
    channel = started_channel(fakes)
    pushed = {dst for _, dst in fakes.adb.pushes}
    assert any(d.endswith("/ma-scrcpy/scrcpy-server.jar") for d in pushed)
    assert "/data/local/tmp/udt/libjpeg-turbo.so" in pushed
    server_cmd = next(c for c in fakes.adb.shells if "app_process" in c)
    assert "1.2.4" in server_cmd
    assert f"udt_socket_name={channel._sock_prefix}" in server_cmd
    assert "tunnel_forward=true" in server_cmd
    assert channel.started


def test_start_idempotent(fakes):
    channel = started_channel(fakes)
    pushes_after_first = len(fakes.adb.pushes)
    channel.start(timeout=0.5)  # 同一实例二次 start:不再推送/启动
    assert len(fakes.adb.pushes) == pushes_after_first
    assert channel.started


def test_sock_prefix_unique_per_instance():
    a = ScrcpyChannel("S1")
    b = ScrcpyChannel("S1")
    assert a._sock_prefix != b._sock_prefix


def test_start_connect_timeout_teardown(fakes):
    fakes.ctrl_cls_state["fail_init"] = True
    channel = ScrcpyChannel("SERIAL")
    with pytest.raises(StreamUnavailable):
        channel.start(timeout=0.3)
    assert not channel.started
    assert fakes.adb.stream.closed  # server shell 流已回收
    assert channel.healthy() is False


def test_missing_asset_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(scrcpy_channel, "_ASSETS_DIR", tmp_path)
    channel = ScrcpyChannel("SERIAL")
    with pytest.raises(StreamUnavailable, match="fetch_scrcpy_server"):
        channel.start()


# ── 注入:像素坐标打包 ─────────────────────────────────────


def unpack_touch(raw: bytes):
    assert raw[0] == 2  # ControlType.INJECT_TOUCH_EVENT
    return _TOUCH_FIELDS.unpack(raw[1:])


_TOUCH_FIELDS = struct.Struct(">BqiiHHHi")


def test_touch_packs_device_pixels(fakes):
    channel = started_channel(fakes)
    channel.touch(540, 1200, Action.DOWN)
    ctrl = FakeCtrl.instances[-1]
    unpacked = unpack_touch(ctrl.sent[-1])
    assert unpacked == (
        0, -1, 540, 1200, SCREEN_W, SCREEN_H, 0xFFFF, 1,
    )


def test_touch_corner_pixel_not_rescaled(fakes):
    """(1,1) 像素不得被当作 0-1 比例放大(wetest touch 的坑)。"""
    channel = started_channel(fakes)
    channel.touch(1, 1, Action.DOWN)
    ctrl = FakeCtrl.instances[-1]
    _, _, x, y, _, _, _, _ = unpack_touch(ctrl.sent[-1])
    assert (x, y) == (1, 1)


def test_touch_clamps_out_of_range(fakes):
    channel = started_channel(fakes)
    channel.touch(99999, -50, Action.UP)
    ctrl = FakeCtrl.instances[-1]
    _, _, x, y, _, _, _, _ = unpack_touch(ctrl.sent[-1])
    assert (x, y) == (SCREEN_W, 0)


def test_swipe_down_move_up_sequence(fakes):
    channel = started_channel(fakes)
    channel.swipe(0, 0, 100, 0, duration_s=0.01)
    ctrl = FakeCtrl.instances[-1]
    actions = [unpack_touch(raw)[0] for raw in ctrl.sent]
    xs = [unpack_touch(raw)[3] for raw in ctrl.sent]
    assert actions[0] == int(Action.DOWN)
    assert actions[-1] == int(Action.UP)
    assert set(actions[1:-1]) == {int(Action.MOVE)}
    assert xs == sorted(xs)  # x 单调递增到 100


# ── 画面与生命周期 ────────────────────────────────────────


def test_screen_size_prefers_wm_size(fakes):
    """屏幕尺寸以 wm size 为准:GET_SCREENINFO 启动竞态返回过缩放尺寸。"""
    channel = started_channel(fakes)
    assert channel.screen_size() == (SCREEN_W, SCREEN_H)


def test_screen_size_falls_back_to_screen_info(fakes, monkeypatch):
    channel = started_channel(fakes)
    original = fakes.adb.shell

    def no_wm(cmd, stream=False):
        if stream:
            return original(cmd, stream=True)
        return ""  # wm size 不可用

    fakes.adb.shell = no_wm
    monkeypatch.setattr(FakeScreenInfo, "Width", SCREEN_W)
    monkeypatch.setattr(FakeScreenInfo, "Height", SCREEN_H)
    channel.refresh_screen_size()
    assert channel.screen_size() == (SCREEN_W, SCREEN_H)


def test_refresh_before_start_raises(fakes):
    channel = ScrcpyChannel("SERIAL")
    with pytest.raises(StreamUnavailable):
        channel.refresh_screen_size()


def test_latest_frame_jpeg_with_size(fakes):
    import cv2

    channel = started_channel(fakes)
    jpeg, width, height = channel.latest_frame()
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) is not None
    assert (width, height) == (SCREEN_W // 5, SCREEN_H // 5)


def test_capture_png_native_resolution(fakes):
    import cv2

    channel = started_channel(fakes)
    png = channel.capture_png()
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (SCREEN_H, SCREEN_W)


def test_capture_failure_recycles_channel(fakes):
    channel = started_channel(fakes)
    ctrl = FakeCtrl.instances[-1]

    def broken(max_size=0, quality=0):
        raise RuntimeError("empty frame")

    ctrl.screenshot = broken
    with pytest.raises(StreamUnavailable):
        channel.capture_png()
    assert not channel.started
    assert ctrl.closed


def test_not_started_operations_raise(fakes):
    channel = ScrcpyChannel("SERIAL")
    for call in (
        lambda: channel.latest_frame(),
        lambda: channel.capture_png(),
        lambda: channel.touch(1, 1, Action.DOWN),
        lambda: channel.screen_size(),
    ):
        with pytest.raises(StreamUnavailable):
            call()


def test_healthy_lifecycle(fakes):
    channel = ScrcpyChannel("SERIAL")
    assert channel.healthy() is False
    channel.start(timeout=0.5)
    assert channel.healthy() is True
    channel.stop()
    assert channel.healthy() is False
    channel.stop()  # 幂等
    assert not channel.started
