"""上游协议编解码单元测试(SU-01):对照 v5.0.1 源码的逐字节断言。"""

import struct

import pytest

from mobile_auto.stream import upstream_proto as proto


class TestSocketName:
    def test_scid_negative_uses_bare_prefix(self):
        assert proto.socket_name_for(-1) == "scrcpy"

    def test_scid_formats_8_hex(self):
        assert proto.socket_name_for(0x35A74D33) == "scrcpy_35a74d33"
        assert proto.socket_name_for(1) == "scrcpy_00000001"


class TestScidOwnership:
    def test_encode_decode_roundtrip(self):
        scid = proto.encode_scid(26804, 5)
        assert 0 < scid < 2**31
        assert proto.decode_scid_host_mod(scid) == 26804

    def test_encode_bounds(self):
        with pytest.raises(ValueError):
            proto.encode_scid(100_000, 0)  # pid 模值域 [0, 99999]
        with pytest.raises(ValueError):
            proto.encode_scid(0, 256)


class TestKeycode:
    def test_back_keydown_bytes(self):
        # type=0, action=0(DOWN), keycode=4(BACK), repeat=0, metaState=0
        assert proto.encode_keycode(proto.KEY_ACTION_DOWN, proto.KEYCODE_BACK) == (
            struct.pack(">BBiii", 0, 0, 4, 0, 0)
        )

    def test_home_keyup(self):
        data = proto.encode_keycode(proto.KEY_ACTION_UP, proto.KEYCODE_HOME)
        assert data[0] == 0 and data[1] == 1
        assert struct.unpack(">i", data[2:6])[0] == 3

    def test_wake_keycode(self):
        data = proto.encode_keycode(proto.KEY_ACTION_DOWN, proto.KEYCODE_WAKEUP)
        assert struct.unpack(">i", data[2:6])[0] == 224

    def test_total_length_14(self):
        assert len(proto.encode_keycode(0, 4)) == 1 + 1 + 4 + 4 + 4


class TestTouch:
    def test_down_full_field_layout(self):
        data = proto.encode_touch(proto.ACTION_DOWN, 540, 1200, 1080, 2400)
        expect = struct.pack(
            ">BBqiiHHHii",
            2,          # TYPE_INJECT_TOUCH_EVENT
            0,          # ACTION_DOWN
            -1,         # POINTER_ID_GENERIC_FINGER
            540, 1200,  # x, y
            1080, 2400, # screen
            0xFFFF,     # pressure ≈1.0
            0, 0,       # actionButton, buttons
        )
        assert data == expect
        assert len(data) == 1 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4 + 4

    def test_action_values_match_android_motionevent(self):
        """Android MotionEvent:DOWN=0/UP=1/MOVE=2;写反则 tap 永不抬指(真机实证)。"""
        assert proto.ACTION_DOWN == 0
        assert proto.ACTION_UP == 1
        assert proto.ACTION_MOVE == 2
        move = proto.encode_touch(proto.ACTION_MOVE, 1, 1, 10, 10)
        up = proto.encode_touch(proto.ACTION_UP, 1, 1, 10, 10)
        assert move[1] == 2 and up[1] == 1

    def test_tap_sequence_is_down_then_up(self):
        down, up = proto.tap_sequence(3, 4, 100, 200)
        assert down[1] == 0 and up[1] == 1
        assert down[2:] == up[2:]  # 同点同屏


class TestVideoPackets:
    def test_session_meta_roundtrip(self):
        # 按 Streamer.writeSessionMeta 编码:flags(i32, bit31→Java int 为负) + w + h
        raw = struct.pack(">iII", -(1 << 31), 496, 1080)
        meta = proto.parse_session_meta(raw)
        assert meta == proto.SessionMeta(width=496, height=1080)

    def test_session_meta_rejects_non_meta_flags(self):
        with pytest.raises(ValueError, match="bit31"):
            proto.parse_session_meta(struct.pack(">iII", 0, 1, 1))

    def test_frame_packet_config(self):
        # ptsAndFlags: CONFIG(bit62);size=4
        head = struct.pack(">qi", 1 << 62, 4)
        pkt = proto.parse_video_packet(head + b"SPS\n")
        assert pkt.pts_us is None and pkt.key_frame is False
        assert pkt.payload == b"SPS\n"

    def test_frame_packet_key_frame_with_pts(self):
        pts = 1_234_567
        head = struct.pack(">qi", pts | (1 << 61), 2)
        pkt = proto.parse_video_packet(head + b"\x00\x00")
        assert pkt.pts_us == pts and pkt.key_frame is True
        assert pkt.payload == b"\x00\x00"

    def test_frame_packet_delta(self):
        head = struct.pack(">qi", 42, 1)
        pkt = proto.parse_video_packet(head + b"\x01")
        assert pkt.pts_us == 42 and pkt.key_frame is False

    def test_truncated_payload_raises(self):
        head = struct.pack(">qi", 42, 10)
        with pytest.raises(ValueError, match="不完整"):
            proto.parse_video_packet(head + b"\x01")

    def test_session_meta_flag_on_i64_head(self):
        assert proto.is_session_meta(1 << 63) is True
        assert proto.is_session_meta(1 << 62) is False
