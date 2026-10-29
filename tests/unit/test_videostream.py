"""二进制帧封装单元测试(web-preview 02):线格式、校验、往返。"""

import struct

import pytest

from mobile_auto.errors import StreamUnavailable
from mobile_auto.stream.upstream import EncodedPacket
from mobile_auto.viewer.videostream import (
    FLAG_KEY,
    KIND_CONFIG,
    KIND_FRAME,
    KIND_SESSION,
    PROTOCOL_VERSION,
    encode_relay_packet,
    parse_relay_header,
)


def test_header_is_27_bytes_network_order():
    packet = EncodedPacket("frame", b"\x01\x02", True, 1234, 1080, 2400)
    message = encode_relay_packet(packet, generation=7, sequence=3)
    assert len(message) == 27 + 2
    assert struct.unpack(">B", message[:1])[0] == PROTOCOL_VERSION
    assert struct.unpack(">B", message[1:2])[0] == KIND_FRAME
    assert struct.unpack(">I", message[2:6])[0] == 7  # generation
    assert struct.unpack(">I", message[6:10])[0] == 3  # sequence
    assert struct.unpack(">Q", message[10:18])[0] == 1234  # pts_us
    flags = struct.unpack(">B", message[18:19])[0]
    assert flags & FLAG_KEY
    assert struct.unpack(">H", message[19:21])[0] == 1080
    assert struct.unpack(">H", message[21:23])[0] == 2400
    assert struct.unpack(">I", message[23:27])[0] == 2  # payload_length
    assert message[27:] == b"\x01\x02"


def test_roundtrip_all_kinds():
    cases = [
        (EncodedPacket("config", b"SPSPPS", False, 0, 0, 0), KIND_CONFIG),
        (EncodedPacket("session", b"", False, 0, 720, 1600), KIND_SESSION),
        (EncodedPacket("frame", b"au", True, 33_000, 720, 1600), KIND_FRAME),
    ]
    for packet, kind in cases:
        message = encode_relay_packet(packet, generation=1, sequence=0)
        header = parse_relay_header(message[:27])
        assert header["kind"] == kind
        assert header["payload_length"] == len(packet.payload)
        assert message[27:] == packet.payload


def test_unknown_kind_rejected():
    packet = EncodedPacket("bogus", b"", False, 0, 1, 1)
    with pytest.raises(StreamUnavailable):
        encode_relay_packet(packet, generation=1, sequence=0)


def test_oversize_payload_rejected():
    packet = EncodedPacket("frame", b"x" * (2 * 1024 * 1024 + 1), True, 0, 10, 10)
    with pytest.raises(StreamUnavailable):
        encode_relay_packet(packet, generation=1, sequence=0)


def test_oversize_dimension_rejected():
    packet = EncodedPacket("frame", b"", True, 0, 70000, 100)
    with pytest.raises(StreamUnavailable):
        encode_relay_packet(packet, generation=1, sequence=0)


def test_parse_header_validates_length_version_and_kind():
    with pytest.raises(ValueError):
        parse_relay_header(b"\x00" * 26)
    bad_version = struct.pack(">BBIIQBHHI", 2, KIND_FRAME, 0, 0, 0, 0, 1, 1, 0)
    with pytest.raises(ValueError):
        parse_relay_header(bad_version)
    bad_kind = struct.pack(">BBIIQBHHI", 1, 9, 0, 0, 0, 0, 1, 1, 0)
    with pytest.raises(ValueError):
        parse_relay_header(bad_kind)
