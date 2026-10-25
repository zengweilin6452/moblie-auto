"""上游 scrcpy(v5.0.1)协议编解码:纯字节层,无 I/O(SU-01)。

常量与帧格式对照 v5.0.1 源码(.scratch/scrcpy-upstream/refs/):
- ControlMessage.java:type 常量;
- ControlMessageReader.java:各消息字段序(u8/i32/u16/i64,大端);
- Streamer.java:视频流分包(ptsAndFlags i64 + size i32 + 载荷;
  会话元包 flags i32 + width + height);
- Options.java / DesktopConnection.java:scid 与 socket 命名。

只做编码与解析,不触碰 socket:连接/线程在 UpstreamChannel。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ── 控制消息类型(ControlMessage.java)────────────────────
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_BACK_OR_SCREEN_ON = 4

# MotionEvent.ACTION_*(注入 touch 用;Controller.java 侧同义)
ACTION_DOWN = 0
ACTION_MOVE = 1
ACTION_UP = 2

# KeyEvent(Action=0 DOWN / 1 UP)
KEY_ACTION_DOWN = 0
KEY_ACTION_UP = 1

# android.view.KeyEvent 键值(Controller.java 注入路径)
KEYCODE_BACK = 4
KEYCODE_HOME = 3
KEYCODE_WAKEUP = 224

# 通用手指 pointerId(PointersState.POINTER_ID_GENERIC_FINGER = -1)
POINTER_ID_GENERIC_FINGER = -1

# pressure u16 定点满量(Binary.u16FixedPointToFloat(0xFFFF)≈1.0)
PRESSURE_MAX_U16 = 0xFFFF

_SOCKET_NAME_PREFIX = "scrcpy"


def socket_name_for(scid: int) -> str:
    """scid → localabstract socket 名(DesktopConnection.getSocketName)。"""
    if scid < 0:
        return _SOCKET_NAME_PREFIX
    return f"{_SOCKET_NAME_PREFIX}_{scid:08x}"


def encode_scid(host_pid_mod: int, seq: int) -> int:
    """宿主 pid 模值与通道序号 → 31 位 scid(自愈反解宿主用)。

    (pid_mod << 8) | seq;pid_mod ≤ 99999(<2^17),scid < 2^25 ≤ 2^31-1。
    """
    if not 0 <= host_pid_mod < 100_000:
        raise ValueError(f"host_pid_mod 越界:{host_pid_mod}")
    if not 0 <= seq < 256:
        raise ValueError(f"seq 越界:{seq}")
    return (host_pid_mod << 8) | seq


def decode_scid_host_mod(scid: int) -> int:
    """scid → 宿主 pid 模值(残留自愈判定用)。"""
    return scid >> 8


# ── 控制消息编码(字段序对照 ControlMessageReader.java)───


def encode_keycode(
    action: int, keycode: int, *, repeat: int = 0, meta_state: int = 0
) -> bytes:
    """TYPE_INJECT_KEYCODE:type u8 + action u8 + keycode i32 + repeat i32 + metaState i32。"""
    return struct.pack(
        ">BBiii", TYPE_INJECT_KEYCODE, action, keycode, repeat, meta_state
    )


def encode_touch(
    action: int,
    x: int,
    y: int,
    screen_width: int,
    screen_height: int,
    *,
    pointer_id: int = POINTER_ID_GENERIC_FINGER,
    pressure_u16: int = PRESSURE_MAX_U16,
    action_button: int = 0,
    buttons: int = 0,
) -> bytes:
    """TYPE_INJECT_TOUCH_EVENT:type u8 + action u8 + pointerId i64 + x i32 +
    y i32 + screenWidth u16 + screenHeight u16 + pressure u16 + actionButton i32 +
    buttons i32(parseInjectTouchEvent 字段序)。坐标为设备像素。"""
    return struct.pack(
        ">BBqiiHHHii",
        TYPE_INJECT_TOUCH_EVENT,
        action,
        pointer_id,
        x,
        y,
        screen_width,
        screen_height,
        pressure_u16,
        action_button,
        buttons,
    )


def tap_sequence(
    x: int, y: int, screen_width: int, screen_height: int
) -> list[bytes]:
    """单击 = DOWN → UP 两消息(按住时长由调用方 sleep,与 wetest tap 同形)。"""
    return [
        encode_touch(ACTION_DOWN, x, y, screen_width, screen_height),
        encode_touch(ACTION_UP, x, y, screen_width, screen_height),
    ]


# ── 视频流解析(Streamer.java)────────────────────────────

_PACKET_FLAG_CONFIG = 1 << 62
_PACKET_FLAG_KEY_FRAME = 1 << 61
_PACKET_FLAG_SESSION = 1 << 63


@dataclass(frozen=True)
class SessionMeta:
    """会话元包:视频分辨率(分辨率变化时 server 重发)。"""

    width: int
    height: int


@dataclass(frozen=True)
class VideoPacket:
    """一个视频分包;config=True 时载荷为解码器 extradata(SPS/PPS)。"""

    pts_us: int | None  # None = CONFIG 包(无 pts)
    key_frame: bool
    payload: bytes


def parse_session_meta(payload: bytes) -> SessionMeta:
    """会话元包体:flags(i32,bit31 置位;Java int 按位读,负值合法)+ width +
    height(i32)。宽度/高度按无符号解释(u32 语义, struct 用 I)。"""
    if len(payload) != 12:
        raise ValueError(f"会话元包长度异常:{len(payload)}")
    flags, width, height = struct.unpack(">iII", payload)
    if not flags & _PACKET_FLAG_SESSION:
        raise ValueError("非会话元包(flags bit31 未置位)")
    return SessionMeta(width=width, height=height)


def is_session_meta(flags_and_pts: int) -> bool:
    """包头 i64 的 bit63 置位 = 会话元包(Streamer.PACKET_FLAG_SESSION)。"""
    return bool(flags_and_pts & _PACKET_FLAG_SESSION)


def parse_video_packet(payload: bytes) -> VideoPacket:
    """帧包头:ptsAndFlags(i64)+ size(i32),随后为媒体载荷。"""
    if len(payload) < 12:
        raise ValueError(f"帧包头长度异常:{len(payload)}")
    pts_and_flags, size = struct.unpack(">qi", payload[:12])
    body = payload[12 : 12 + size]
    if len(body) != size:
        raise ValueError(f"帧载荷不完整:期望 {size},实际 {len(body)}")
    if pts_and_flags & _PACKET_FLAG_CONFIG:
        return VideoPacket(pts_us=None, key_frame=False, payload=body)
    key_frame = bool(pts_and_flags & _PACKET_FLAG_KEY_FRAME)
    pts = pts_and_flags & ~(
        _PACKET_FLAG_CONFIG | _PACKET_FLAG_KEY_FRAME | (1 << 63)
    )
    return VideoPacket(pts_us=pts, key_frame=key_frame, payload=body)
