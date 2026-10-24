"""scrcpy 视频流/控制通道(P1 工单 01)。

独立于 u2 的第二条设备通道:复用 wetest-scrcpy 客户端的协议实现
(ctrl 控制/服务端截图、h264 视频流解码),但 server 生命周期自管——
推送随包资产并启动进程唯一 socket 前缀的 server,viewer 与 pytest
进程各自持有、互不复用(跨进程共享 server 的所有权与回收不可靠)。

通道错误是能力降级信号(上层回退 u2 截图),不是设备会话故障:
统一抛 StreamUnavailable,不触碰会话状态机。wetest/adbutils 延迟导入,
保持包导入无设备副作用(与 backend_u2 同约定)。
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

from ..errors import StreamUnavailable

_ASSETS_DIR = Path(__file__).parent / "assets"
_DEVICE_ROOT = "/data/local/tmp/ma-scrcpy"
# server(wetest 版 3.3.2.11)硬编码从 /data/local/tmp/udt/ 加载 turbojpeg
# (dlopen 报错路径即证据;自定义目录参数无效),so 必须推到该固定路径
_LIBJPEG_REMOTE = "/data/local/tmp/udt/libjpeg-turbo.so"
_START_TIMEOUT_SECONDS = 15.0
# 触控消息包格式与 wetest CtrlConnection.touch 一致(协议 INJECT_TOUCH_EVENT);
# 不直接用其 touch():它把 (x<=1,y<=1) 的像素误判为 0-1 比例坐标再放大
_TOUCH_MSG = struct.Struct(">BqiiHHHi")
_PREFIX_SEQ = 0  # 进程内实例序号:同一进程反复启停不复用旧 server 的 socket 名


def _asset(name: str) -> Path:
    path = _ASSETS_DIR / name
    if not path.is_file():
        raise StreamUnavailable(
            f"缺少 scrcpy 资产 {name};先运行 python scripts/fetch_scrcpy_server.py"
        )
    return path


class ScrcpyChannel:
    """单台设备的 scrcpy 视频流与控制通道;方法同步,跨线程经设备队列串行调用。"""

    def __init__(
        self,
        serial: str,
        *,
        sock_prefix: str | None = None,
        max_size: int = 1080,
        bit_rate: int = 4_000_000,
        max_fps: int = 20,
        jpeg_quality: int = 80,
    ) -> None:
        global _PREFIX_SEQ
        self._serial = serial
        # 前缀进程内实例唯一:同进程重启不复用旧 socket;跨进程天然不同
        self._sock_prefix = (
            sock_prefix or f"ma-{os.getpid() % 100000}-{_PREFIX_SEQ}"
        )
        _PREFIX_SEQ += 1
        self._max_size = max_size
        self._bit_rate = bit_rate
        self._max_fps = max_fps
        self._jpeg_quality = jpeg_quality
        self._lock = threading.RLock()
        self._started = False
        self._server_stream = None  # adb shell 流;持有引用避免被回收关断 server
        self._wet_device = None
        self._ctrl = None
        self._video = None
        self._screen: tuple[int, int] | None = None

    @property
    def started(self) -> bool:
        return self._started

    def healthy(self) -> bool:
        """通道可用性:已启动、视频泵线程存活且 ctrl 连接未断。"""
        with self._lock:
            if not self._started or self._ctrl is None or self._video is None:
                return False
            import socket

            sock_alive = True
            try:
                raw = self._ctrl._sock  # wetest StreamSocket 的底层 socket
                sock_alive = isinstance(raw, socket.socket) and raw.fileno() != -1
            except AttributeError:
                sock_alive = False
            return sock_alive and self._video._thread is not None and self._video._thread.is_alive()

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self, timeout: float = _START_TIMEOUT_SECONDS) -> None:
        """推送资产、启动 server 并建立 ctrl/video 连接;幂等(已启动直接返回)。"""
        with self._lock:
            if self._started:
                return
            jar_local = _asset("scrcpy-server")
            so_local = _asset("libjpeg-turbo.so")
            adb_device = self._adb_device()
            jar_remote = f"{_DEVICE_ROOT}/scrcpy-server.jar"
            try:
                adb_device.shell(f"mkdir -p {_DEVICE_ROOT} /data/local/tmp/udt")
                adb_device.sync.push(str(jar_local), jar_remote)
                adb_device.sync.push(str(so_local), _LIBJPEG_REMOTE)
            except Exception as exc:
                raise StreamUnavailable(f"推送 scrcpy 资产失败:{exc}") from exc
            commands = [
                f"CLASSPATH={jar_remote}",
                "app_process",
                "/",
                "com.genymobile.scrcpy.Server",
                "1.2.4",
                "log_level=info",
                f"max_size={self._max_size}",
                f"bit_rate={self._bit_rate}",
                f"max_fps={self._max_fps}",
                "tunnel_forward=true",
                f"udt_socket_name={self._sock_prefix}",
                "clipboard_autosync=true",
                "audio_codec=raw",
            ]
            try:
                self._server_stream = adb_device.shell(commands, stream=True)
            except Exception as exc:
                raise StreamUnavailable(f"启动 scrcpy server 失败:{exc}") from exc
            try:
                self._connect(adb_device, timeout)
            except BaseException:
                self._teardown()
                raise
            self._started = True

    def _connect(self, adb_device, timeout: float) -> None:
        from wetest.scrcpy import AndroidDevice, CtrlConnection, VideoStreamScreenshot

        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while True:
            try:
                self._wet_device = AndroidDevice(
                    serial=self._serial,
                    sock_prefix=self._sock_prefix,
                    launcher_config=None,
                )
                self._ctrl = CtrlConnection(device=self._wet_device, timeout=5)
                break
            except Exception as exc:
                last_exc = exc
                self._wet_device = None
                self._ctrl = None
                if time.monotonic() >= deadline:
                    raise StreamUnavailable(
                        f"scrcpy server {self._sock_prefix} 连接超时({timeout}s);"
                        "server 可能启动失败,详见设备端日志"
                    ) from last_exc
                time.sleep(0.25)
        self._video = VideoStreamScreenshot(device=self._wet_device, timeout=5)
        self._video.start()
        self._screen = self._resolve_screen(adb_device)

    def _resolve_screen(self, adb_device) -> tuple[int, int]:
        """屏幕尺寸:wm size 为权威(GET_SCREENINFO 启动竞态时返回过视频缩放
        尺寸,不可作触摸坐标基准);旋转 90/270 时交换宽高。"""
        width = height = None
        try:
            out = adb_device.shell("wm size")
            for part in out.replace("\n", " ").split():
                if "x" in part and part[0].isdigit():
                    width, height = (int(v) for v in part.split("x", 1))
                    break
        except Exception:
            pass
        if not width or not height:
            info = self._ctrl.get_screen_info()
            width, height = int(info.Width), int(info.Height)
            return width, height
        try:
            rotation = int(self._ctrl.get_rotation())
        except Exception:
            rotation = 0
        if rotation % 2 == 1:
            width, height = height, width
        return width, height

    def refresh_screen_size(self) -> None:
        """旋转/折叠等显示变化后重探屏幕尺寸(不重启 server)。"""
        with self._lock:
            if not self._started:
                raise StreamUnavailable("scrcpy 通道未启动")
            self._screen = self._resolve_screen(self._adb_device())

    def stop(self) -> None:
        """关闭连接并结束 server;幂等。释放不回滚已注入的操作。"""
        with self._lock:
            self._teardown()
            self._started = False

    def _teardown(self) -> None:
        for closer in (self._ctrl, self._video):
            if closer is not None:
                try:
                    closer.close()
                except Exception:
                    pass
        self._ctrl = None
        self._video = None
        self._wet_device = None
        self._screen = None
        if self._server_stream is not None:
            try:
                self._server_stream.close()  # 关 stdin 使 app_process 退出
            except Exception:
                pass
            self._server_stream = None

    def _adb_device(self):
        import adbutils

        return adbutils.adb.device(serial=self._serial)

    # ── 画面 ─────────────────────────────────────────────────

    def latest_frame(self) -> tuple[bytes, int, int]:
        """视频流最新帧,编码 JPEG;返回 (jpeg, width, height)。"""
        try:
            frame = self._require_video().get_screenshot()
        except Exception as exc:
            raise StreamUnavailable(f"视频流取帧失败:{exc}") from exc
        return self._encode_jpeg(frame)

    def capture_png(self) -> bytes:
        """ctrl CAPTURE 服务端截图(原始分辨率、质量 95),PNG 字节。

        空帧意味着 server 侧 turbojpeg 加载/编码失败,实测会连坐断开视频
        socket:按通道损坏处理,回收连接,由上层重启或回退 u2。
        """
        import cv2

        try:
            arr = self._require_ctrl().screenshot(max_size=8192, quality=95)
        except Exception as exc:
            self.stop()
            raise StreamUnavailable(
                f"scrcpy CAPTURE 失败(空帧或连接断开,通道已回收):{exc}"
            ) from exc
        ok, buf = cv2.imencode(".png", arr)  # screenshot 返回 BGR,cv2 原生顺序
        if not ok:
            raise StreamUnavailable("scrcpy 截图编码失败")
        return buf.tobytes()

    def _encode_jpeg(self, frame) -> tuple[bytes, int, int]:
        import cv2

        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        if not ok:
            raise StreamUnavailable("视频帧 JPEG 编码失败")
        height, width = frame.shape[:2]
        return buf.tobytes(), width, height

    # ── 控制注入(设备像素坐标)───────────────────────────────

    def screen_size(self) -> tuple[int, int]:
        """当前屏幕尺寸(设备像素,含旋转);start 时解析,变化后 refresh。"""
        with self._lock:
            if self._screen is None:
                raise StreamUnavailable("scrcpy 通道未启动")
            return self._screen

    def touch(self, x: int, y: int, action) -> None:
        """注入触摸;action 取 wetest.scrcpy.Action 枚举。坐标为设备像素。"""
        from wetest.scrcpy.const import ControlType

        ctrl = self._require_ctrl()
        width, height = self.screen_size()
        x = min(max(int(x), 0), width)
        y = min(max(int(y), 0), height)
        payload = _TOUCH_MSG.pack(int(action), -1, x, y, width, height, 0xFFFF, 1)
        self._send(ctrl, ControlType.INJECT_TOUCH_EVENT, payload)

    def tap(self, x: int, y: int) -> None:
        """单击:DOWN → 短暂按住 → UP;坐标为设备像素。"""
        import time as _time

        from wetest.scrcpy import Action

        self.touch(x, y, Action.DOWN)
        _time.sleep(0.02)
        self.touch(x, y, Action.UP)

    def press_key(self, name: str) -> None:
        """注入有限按键(back/home);未知名称报 InvalidTarget 语义错误。"""
        from wetest.scrcpy import KeyCode

        from ..errors import InvalidTarget

        mapping = {"back": KeyCode.BACK, "home": KeyCode.HOME}
        if name not in mapping:
            raise InvalidTarget(f"不支持的按键:{name!r};支持:back / home")
        try:
            self._require_ctrl().press_button(mapping[name])
        except Exception as exc:
            raise StreamUnavailable(f"scrcpy 按键注入失败:{exc}") from exc

    def wake(self) -> None:
        """点亮屏幕(不熄屏、不旋转)。"""
        from wetest.scrcpy import PowerMode

        try:
            self._require_ctrl().set_screen_power_mode(PowerMode.NORMAL)
        except Exception as exc:
            raise StreamUnavailable(f"scrcpy 亮屏失败:{exc}") from exc

    def swipe(
        self, x0: int, y0: int, x1: int, y1: int, duration_s: float = 0.3
    ) -> None:
        """直线滑动:DOWN → 等距 MOVE → UP,总时长约 duration_s。"""
        import time as _time

        from wetest.scrcpy import Action

        self.touch(x0, y0, Action.DOWN)
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / 5))
        delay = max(duration_s / steps, 0.001)
        for i in range(1, steps + 1):
            self.touch(
                x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps, Action.MOVE
            )
            _time.sleep(delay)
        self.touch(x1, y1, Action.UP)

    def _send(self, ctrl, control_type, payload: bytes) -> None:
        """走 ctrl 公共 write 通道发控制消息(绕开 touch 的 0-1 比例歧义)。"""
        message = struct.pack(">B", int(control_type)) + payload
        try:
            ctrl.write(message)
        except Exception as exc:
            raise StreamUnavailable(f"scrcpy 控制消息发送失败:{exc}") from exc

    # ── 内部 ─────────────────────────────────────────────────

    def _require_ctrl(self):
        with self._lock:
            if not self._started or self._ctrl is None:
                raise StreamUnavailable("scrcpy 通道未启动")
            return self._ctrl

    def _require_video(self):
        with self._lock:
            if not self._started or self._video is None:
                raise StreamUnavailable("scrcpy 通道未启动")
            return self._video


__all__ = ["ScrcpyChannel"]
