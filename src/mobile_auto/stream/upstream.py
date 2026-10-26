"""上游 scrcpy(v5.0.1)视频/控制通道(SU-01)。

与 wetest 的 ScrcpyChannel 同接口(start/stop/healthy/latest_frame/
capture_png/screen_size/refresh_screen_size/touch/tap/press_key/wake/swipe),
供 supervisor/驱动/viewer 无差别使用。协议对照 v5.0.1 源码逐项核实
(.scratch/scrcpy-upstream/spec.md §4;源码 refs/):

- 启动:CLASSPATH app_process com.genymobile.scrcpy.Server 5.0.1 scid=<hex> …
  tunnel_forward=true video=true audio=false control=true;
- socket:server 建 LocalServerSocket `scrcpy_%08x`,按 video→control 顺序
  accept;首个连接后 server 写 1 字节 dummy,客户端读一次探测连接错误;
  客户端经 adbutils create_connection("localabstract", …) 连接(不占端口);
- 视频:会话元包(flags bit31 + w + h)与帧包(ptsAndFlags i64 + size i32 +
  载荷;CONFIG 包为 extradata),PyAV 解码单帧最新化;
- 控制:type0 KEYCODE(back/home/wake)、type2 TOUCH(DOWN 0/MOVE 1/UP 2,
  pressure u16 定点)。

通道错误是能力降级信号(StreamUnavailable),不触碰会话状态机——与
wetest 通道同约定。adbutils 延迟导入,包导入无设备副作用。
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

from ..errors import StreamUnavailable
from . import selfheal
from . import upstream_proto as proto
from .upstream_proto import (
    encode_keycode,
    encode_scid,
    encode_touch,
    parse_session_meta,
    parse_video_packet,
    socket_name_for,
    tap_sequence,
)

_ASSETS_DIR = Path(__file__).parent / "assets"
_DEVICE_ROOT = "/data/local/tmp/ma-scrcpy"
_UPSTREAM_JAR = "scrcpy-server-upstream.jar"
_SERVER_VERSION = "5.0.1"
_START_TIMEOUT_SECONDS = 15.0
_SEQ_LOCK = threading.Lock()
_SEQ = 0  # 进程内通道序号:scid 与重启不复用


def _next_seq() -> int:
    global _SEQ
    with _SEQ_LOCK:
        seq = _SEQ
        _SEQ += 1
    return seq


def upstream_asset() -> Path:
    path = _ASSETS_DIR / _UPSTREAM_JAR
    if not path.is_file():
        raise StreamUnavailable(
            f"缺少上游 scrcpy server {_UPSTREAM_JAR};"
            "先运行 python scripts/fetch_scrcpy_server.py"
        )
    return path


class UpstreamChannel:
    """单台设备的上游 scrcpy 通道;方法同步,跨线程经设备队列串行调用。"""

    # 实际通道标识:动作流水据此记录,由通道实例声明而非调用方臆造
    channel_name = "upstream"
    # 坐标基准来自视频会话元包(可能被 max_size 缩放),无强制重探能力:
    # refresh_screen_size 仅为接口同形而保留,不能修复基准(工单 04)
    can_refresh_screen_size = False

    def __init__(
        self,
        serial: str,
        *,
        scid: int | None = None,
        max_size: int = 1080,
        bit_rate: int = 4_000_000,
        max_fps: int = 20,
        jpeg_quality: int = 80,
    ) -> None:
        self._serial = serial
        self._host_mod = os.getpid() % 100_000
        self._scid = (
            scid if scid is not None else encode_scid(self._host_mod, _next_seq())
        )
        self._sock_prefix = socket_name_for(self._scid)  # 语义对齐 wetest 通道属性
        self._max_size = max_size
        self._bit_rate = bit_rate
        self._max_fps = max_fps
        self._jpeg_quality = jpeg_quality
        self._lock = threading.RLock()
        self._started = False
        self._server_proc = None  # adb shell app_process 流(持有防回收)
        self._device_pid = None  # 设备端 server pid(stop 显式 kill 用)
        self._video_sock = None
        self._control_sock = None
        self._screen: tuple[int, int] | None = None
        self._decoder = None
        self._latest_jpeg: bytes | None = None
        self._latest_size: tuple[int, int] | None = None
        self._decode_error: str | None = None
        self._pump_thread: threading.Thread | None = None

    # ── 生命周期 ─────────────────────────────────────────────

    @property
    def started(self) -> bool:
        return self._started

    @property
    def scid(self) -> int:
        return self._scid

    def healthy(self) -> bool:
        """通道可用性:已启动、泵线程存活、控制 socket 未断。"""
        with self._lock:
            if not self._started or self._pump_thread is None:
                return False
            if self._decode_error is not None:
                return False
            if not self._pump_thread.is_alive():
                return False
            return self._sock_alive(self._control_sock)

    @staticmethod
    def _sock_alive(sock) -> bool:
        import socket

        raw = getattr(sock, "_sock", None)  # adbutils AdbConnection 包裹的 socket
        if isinstance(raw, socket.socket):
            return raw.fileno() != -1
        return sock is not None

    def start(self, timeout: float = _START_TIMEOUT_SECONDS) -> None:
        """推送资产、启动 server、双 socket 连接并起解码泵;幂等。"""
        with self._lock:
            if self._started:
                return
            adb_device = self._adb_device()
            jar_remote = f"{_DEVICE_ROOT}/{_UPSTREAM_JAR}"
            try:
                adb_device.shell(f"mkdir -p {_DEVICE_ROOT}")
                adb_device.sync.push(str(upstream_asset()), jar_remote)
            except Exception as exc:
                raise StreamUnavailable(f"推送上游 scrcpy 资产失败:{exc}") from exc
            commands = [
                f"CLASSPATH={jar_remote}",
                "app_process",
                "/",
                "com.genymobile.scrcpy.Server",
                _SERVER_VERSION,
                f"scid={self._scid:08x}",
                "log_level=info",
                "video=true",
                "audio=false",
                "control=true",
                "send_device_meta=false",  # 首socket 64B 设备名块(默认true),关掉简化流
                "video_codec=h264",
                "tunnel_forward=true",
                f"max_size={self._max_size}",
                f"video_bit_rate={self._bit_rate}",
                f"max_fps={self._max_fps}",
                "stay_awake=false",
            ]
            try:
                self._launch_and_connect(adb_device, commands, timeout)
            except StreamUnavailable as exc:
                # 启动/连接失败 → 残留自愈(双前缀:ma- comm 与 scid cmdline),
                # 清得残留则重试一次;与 wetest 通道同语义(工单 02)
                report = selfheal.kill_stale_servers(
                    adb_device, serial=self._serial, own_mod_pid=self._host_mod
                )
                if report.killed == 0:
                    raise StreamUnavailable(
                        f"{exc};残留自愈未清得任何 server:{report.note}"
                    ) from exc
                self._launch_and_connect(adb_device, commands, timeout)
            self._device_pid = self._find_server_pid(adb_device)
            self._started = True
            with selfheal.LIVE_PREFIXES_LOCK:
                selfheal.LIVE_PREFIXES.add(self._sock_prefix)

    def _launch_and_connect(self, adb_device, commands, timeout: float) -> None:
        try:
            self._server_proc = adb_device.shell(commands, stream=True)
        except Exception as exc:
            raise StreamUnavailable(f"启动上游 scrcpy server 失败:{exc}") from exc
        try:
            self._connect(adb_device, timeout)
        except BaseException:
            self._teardown()
            raise

    def _connect(self, adb_device, timeout: float) -> None:

        name = self._sock_prefix
        deadline = time.monotonic() + timeout
        # video 先连(server accept 顺序);读 dummy byte 探测连接错误
        while True:
            try:
                self._video_sock = adb_device.create_connection(
                    "localabstract", name
                )
                break
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise StreamUnavailable(
                        f"上游 scrcpy video socket {name} 连接超时({timeout}s);"
                        "server 可能启动失败,详见设备端日志"
                    ) from exc
                time.sleep(0.2)
        try:
            self._video_sock.recv(1)  # server 的 dummy byte
        except Exception as exc:
            raise StreamUnavailable(f"读取 video dummy byte 失败:{exc}") from exc
        try:
            self._control_sock = adb_device.create_connection(
                "localabstract", name
            )
        except Exception as exc:
            raise StreamUnavailable(f"连接 control socket 失败:{exc}") from exc
        self._pump_thread = threading.Thread(
            target=self._video_pump, name="mobile-auto-upstream-video", daemon=True
        )
        self._pump_thread.start()
        pump_deadline = time.monotonic() + timeout
        while self._screen is None:
            if self._decode_error is not None:
                raise StreamUnavailable(f"视频流初始化失败:{self._decode_error}")
            if time.monotonic() >= pump_deadline:
                raise StreamUnavailable(
                    f"等待首个会话元包超时({timeout}s)"
                )
            time.sleep(0.05)

    def _video_pump(self) -> None:
        """读视频 socket → 解码 → 单帧最新化;错误记录后线程退出。

        线格式(v5.0.1 Streamer.java):首包 4 字节 codec id(h264=0x68323634),
        其后会话元包(flags i32 bit31 + w i32 + h i32,12B),其后帧包
        (ptsAndFlags i64 + size i32 + 载荷)。
        """
        import av
        import cv2

        try:
            decoder = self._ensure_decoder()
            buffer = b""
            header_done = False
            while True:
                chunk = self._video_sock.recv(256 * 1024)
                if not chunk:
                    raise ConnectionError("video socket EOF")
                buffer += chunk
                while True:
                    if not header_done:
                        if len(buffer) < 4:
                            break
                        codec_id = struct.unpack(">I", buffer[:4])[0]
                        if codec_id != 0x68323634:  # "h264"
                            raise ValueError(
                                f"视频 codec 头异常:0x{codec_id:08x}(期望 h264)"
                            )
                        header_done = True
                        buffer = buffer[4:]
                        continue
                    if self._screen is None:
                        if len(buffer) < 12:
                            break
                        flags = struct.unpack(">i", buffer[:4])[0]
                        if not flags & (1 << 31):  # PACKET_FLAG_SESSION 的 i32 位
                            raise ValueError("视频流首元包不是会话元包")
                        meta = parse_session_meta(buffer[:12])
                        self._screen = (meta.width, meta.height)
                        buffer = buffer[12:]
                        continue
                    if len(buffer) < 12:
                        break
                    # 元包(12B,flags i32 bit31=负数)与帧包(ptsAndFlags i64,
                    # pts 正值)以首 4 字节 i32 的符号区分(Streamer.java:
                    # writeSessionMeta 裸 12B,writeFrameMeta 带完整 i64 头)
                    first_i32 = struct.unpack(">i", buffer[:4])[0]
                    if first_i32 < 0 and first_i32 & (1 << 31):
                        if len(buffer) < 12:
                            break
                        meta = parse_session_meta(buffer[:12])
                        self._screen = (meta.width, meta.height)
                        buffer = buffer[12:]
                        continue
                    size = struct.unpack(">i", buffer[8:12])[0]
                    if len(buffer) < 12 + size:
                        break
                    packet = parse_video_packet(buffer[: 12 + size])
                    buffer = buffer[12 + size :]
                    if packet.pts_us is None:
                        # CONFIG 包 = 解码器 extradata(SPS/PPS);直接 decode
                        # 会报 InvalidDataError(单包非完整 NAL,真机实证)
                        self._set_extradata(packet.payload)
                        continue
                    try:
                        decoded = decoder.decode(av.Packet(packet.payload))
                    except av.error.InvalidDataError:
                        # 单包解码失败(流启动期/坏包):丢包继续,不杀通道
                        continue
                    for frame in decoded:
                        ok, buf = cv2.imencode(
                            ".jpg",
                            frame.to_ndarray(format="bgr24"),
                            [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
                        )
                        if ok:
                            with self._lock:
                                self._latest_jpeg = buf.tobytes()
                                self._latest_size = (
                                    frame.width,
                                    frame.height,
                                )
        except Exception as exc:  # 泵线程错误:通道降级信号
            self._decode_error = str(exc)

    def _ensure_decoder(self):
        import av

        if self._decoder is None:
            self._decoder = av.CodecContext.create("h264", "r")
        return self._decoder

    def _set_extradata(self, payload: bytes) -> None:
        """CONFIG 包(SPS/PPS)设为解码器 extradata;须在首帧前到达。
        分辨率变化重建解码器以接收新 extradata。"""
        import av

        if self._latest_jpeg is not None:  # 流重启:重建解码器
            self._decoder = av.CodecContext.create("h264", "r")
        self._ensure_decoder().extradata = payload

    def _adb_device(self):
        import adbutils

        return adbutils.adb.device(serial=self._serial)

    def _find_server_pid(self, adb_device) -> int | None:
        """按 scid 定位设备端 server pid(stop 显式回收用;尽力而为)。"""
        try:
            out = adb_device.shell(f"pgrep -f 'scid={self._scid:08x}'")
            return int(out.strip().splitlines()[0]) if out.strip() else None
        except Exception:
            return None

    @staticmethod
    def _sock_alive_control(sock) -> bool:  # 占位:见 healthy()
        return UpstreamChannel._sock_alive(sock)

    # ── 画面 ─────────────────────────────────────────────────

    def latest_frame(self) -> tuple[bytes, int, int]:
        """解码线程的最新帧,JPEG 编码;返回 (jpeg, width, height)。"""
        with self._lock:
            jpeg = self._latest_jpeg
            size = self._latest_size
        if jpeg is None or size is None:
            raise StreamUnavailable(
                self._decode_error or "视频流尚无解码帧"
            )
        return jpeg, size[0], size[1]

    def capture_png(self) -> bytes:
        """当前解码帧编码 PNG(流分辨率;上游无 CAPTURE 消息,spec §5)。"""
        import cv2
        import numpy as np

        jpeg, _, _ = self.latest_frame()
        decoded = cv2.imdecode(
            np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        ok, buf = cv2.imencode(".png", decoded)
        if not ok:
            raise StreamUnavailable("上游截图编码失败")
        return buf.tobytes()

    def refresh_screen_size(self) -> None:
        """接口同形保留:实际不重探(``can_refresh_screen_size = False``)。

        基准 ``_screen`` 来自视频会话元包,可能被 max_size 缩放;旋转/分辨率
        变化时由泵线程在下一会话元包自动更新。主动 ``wm size`` 重探会把
        视频坐标空间替换为设备坐标空间,破坏缩放映射,故不在此实现。
        Session 的基准保鲜对不可刷新通道只比较宽高比,不调用本方法伪装
        刷新成功(工单 04)。
        """
        with self._lock:
            if self._screen is None:
                raise StreamUnavailable("上游 scrcpy 通道未启动")

    # ── 控制注入(设备像素坐标)───────────────────────────

    def screen_size(self) -> tuple[int, int]:
        with self._lock:
            if self._screen is None:
                raise StreamUnavailable("上游 scrcpy 通道未启动")
            return self._screen

    def _send_control(self, message: bytes) -> None:
        try:
            self._control_sock.send(message)
        except Exception as exc:
            raise StreamUnavailable(f"上游控制消息发送失败:{exc}") from exc

    def touch(self, x: int, y: int, action: int) -> None:
        """注入触摸;action 用 upstream_proto.ACTION_*(0/1/2),设备像素。"""
        width, height = self.screen_size()
        x = min(max(int(x), 0), width)
        y = min(max(int(y), 0), height)
        self._send_control(encode_touch(action, x, y, width, height))

    def tap(self, x: int, y: int) -> None:
        down, up = tap_sequence(x, y, *self.screen_size())
        self._send_control(down)
        time.sleep(0.02)
        self._send_control(up)

    def press_key(self, name: str) -> None:
        from ..errors import InvalidTarget

        keycodes = {
            "back": proto.KEYCODE_BACK,
            "home": proto.KEYCODE_HOME,
        }
        if name not in keycodes:
            raise InvalidTarget(f"不支持的按键:{name!r};支持:back / home")
        self._send_control(
            encode_keycode(proto.KEY_ACTION_DOWN, keycodes[name])
        )
        self._send_control(
            encode_keycode(proto.KEY_ACTION_UP, keycodes[name])
        )

    def wake(self) -> None:
        self._send_control(
            encode_keycode(proto.KEY_ACTION_DOWN, proto.KEYCODE_WAKEUP)
        )
        self._send_control(
            encode_keycode(proto.KEY_ACTION_UP, proto.KEYCODE_WAKEUP)
        )

    def swipe(
        self, x0: int, y0: int, x1: int, y1: int, duration_s: float = 0.3
    ) -> None:
        width, height = self.screen_size()
        self.touch(x0, y0, proto.ACTION_DOWN)
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / 5))
        delay = max(duration_s / steps, 0.001)
        for i in range(1, steps + 1):
            self.touch(
                x0 + (x1 - x0) * i // steps,
                y0 + (y1 - y0) * i // steps,
                proto.ACTION_MOVE,
            )
            time.sleep(delay)
        self.touch(x1, y1, proto.ACTION_UP)

    # ── 生命周期收尾 ─────────────────────────────────────────

    def stop(self) -> None:
        """关闭连接并结束 server;幂等。释放不回滚已注入的操作。"""
        with self._lock:
            self._teardown()
            self._started = False

    def _teardown(self) -> None:
        for sock in (self._video_sock, self._control_sock):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self._video_sock = None
        self._control_sock = None
        # 上游 server 不因 stdin EOF 退出(wetest 会):显式 kill 进程,
        # 再关 shell 流兜底;kill 失败仍按幂等关闭,残留由自愈路径兜底
        if self._device_pid:
            try:
                self._adb_device().shell(f"kill {self._device_pid}")
            except Exception:
                pass
            self._device_pid = None
        if self._server_proc is not None:
            try:
                self._server_proc.close()
            except Exception:
                pass
            self._server_proc = None
        with selfheal.LIVE_PREFIXES_LOCK:
            selfheal.LIVE_PREFIXES.discard(self._sock_prefix)


__all__ = ["UpstreamChannel", "upstream_asset"]
