"""浏览器直解视频档的二进制封装(规格 §合同·视频;纯字节层,无 I/O)。

线格式(网络字节序):version u8 | kind u8 | generation u32 | sequence u32 |
pts_us u64 | flags u8(bit0=key) | width u16 | height u16 | payload_length u32,
包头 27 字节,随后为 payload。kind:1=config(Annex B SPS/PPS)、
2=session(payload 空,尺寸在包头)、3=frame(完整访问单元)。
前端 video_player.js 按同一布局解析;此处只编码与校验服务端要下发的包。
"""

from __future__ import annotations

import struct

from ..errors import StreamUnavailable
from ..stream.upstream import EncodedPacket

PROTOCOL_VERSION = 1
KIND_CONFIG = 1
KIND_SESSION = 2
KIND_FRAME = 3
FLAG_KEY = 0x01
_HEADER = struct.Struct(">BBIIQBHHI")
assert _HEADER.size == 27
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 65535
_KINDS = {"config": KIND_CONFIG, "session": KIND_SESSION, "frame": KIND_FRAME}


def encode_relay_packet(
    packet: EncodedPacket, *, generation: int, sequence: int
) -> bytes:
    """EncodedPacket → 二进制消息;越界尺寸/载荷按契约拒绝(服务端断流)。"""
    kind = _KINDS.get(packet.kind)
    if kind is None:
        raise StreamUnavailable(f"未知编码包类型:{packet.kind!r}")
    if len(packet.payload) > MAX_PAYLOAD_BYTES:
        raise StreamUnavailable(
            f"编码包载荷超限:{len(packet.payload)} > {MAX_PAYLOAD_BYTES}"
        )
    width, height = packet.width, packet.height
    if packet.kind != "config":
        if not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION:
            raise StreamUnavailable(f"视频尺寸越界:{width}x{height}")
    return _HEADER.pack(
        PROTOCOL_VERSION,
        kind,
        generation,
        sequence,
        packet.pts_us,
        FLAG_KEY if packet.key else 0,
        width,
        height,
        len(packet.payload),
    ) + packet.payload


def parse_relay_header(header: bytes) -> dict:
    """27 字节包头 → 字段字典(测试与前端解析对照用)。"""
    if len(header) != _HEADER.size:
        raise ValueError(f"包头长度异常:{len(header)}(期望 {_HEADER.size})")
    version, kind, generation, sequence, pts_us, flags, width, height, length = (
        _HEADER.unpack(header)
    )
    if version != PROTOCOL_VERSION:
        raise ValueError(f"协议版本不符:{version}")
    if kind not in _KINDS.values():
        raise ValueError(f"未知 kind:{kind}")
    return {
        "version": version,
        "kind": kind,
        "generation": generation,
        "sequence": sequence,
        "pts_us": pts_us,
        "key": bool(flags & FLAG_KEY),
        "width": width,
        "height": height,
        "payload_length": length,
    }


__all__ = [
    "FLAG_KEY",
    "KIND_CONFIG",
    "KIND_FRAME",
    "KIND_SESSION",
    "MAX_DIMENSION",
    "MAX_PAYLOAD_BYTES",
    "PROTOCOL_VERSION",
    "encode_relay_packet",
    "parse_relay_header",
]
