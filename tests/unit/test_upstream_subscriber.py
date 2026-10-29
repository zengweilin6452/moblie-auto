"""编码包订阅与通道代际单元测试(web-preview 01/02):门控、溢出、代际递增。"""

import threading

from mobile_auto.stream.upstream import (
    EncodedPacket,
    EncodedSubscriber,
    UpstreamChannel,
    _next_generation,
)


def _frame(key, payload=b"f"):
    return EncodedPacket("frame", payload, key, 0, 1080, 2400)


def test_delta_frames_dropped_before_keyframe_sync():
    sub = EncodedSubscriber()
    sub.offer(_frame(False, b"delta-1"))
    assert sub.take(0.01) is None  # 关键帧同步前丢 delta
    sub.offer(_frame(True, b"key"))
    sub.offer(_frame(False, b"delta-2"))
    assert sub.take(0.01).payload == b"key"
    assert sub.take(0.01).payload == b"delta-2"  # 同步后 delta 放行


def test_overflow_sets_flag_and_drops_packets():
    sub = EncodedSubscriber(max_packets=2, max_bytes=1 << 20)
    sub.offer(_frame(True, b"1"))
    sub.offer(_frame(True, b"2"))
    sub.offer(_frame(True, b"3"))  # 超界:清空并置位
    assert sub.overflowed
    assert sub.take(0.01) is None
    sub.offer(_frame(True, b"4"))  # 溢出后不再接收
    assert sub.take(0.01) is None


def test_close_unblocks_taker_and_stops_accepting():
    sub = EncodedSubscriber()
    done = threading.Event()
    result = {}

    def take():
        result["packet"] = sub.take(5.0)
        done.set()

    thread = threading.Thread(target=take, daemon=True)
    thread.start()
    sub.close()
    assert done.wait(1.0)
    assert result["packet"] is None
    sub.offer(_frame(True))
    assert sub.take(0.01) is None


def test_take_timeout_returns_none():
    sub = EncodedSubscriber()
    assert sub.take(0.01) is None


def test_generation_increments_per_rebuild():
    channel_a = UpstreamChannel("UNIT-GEN")
    channel_b = UpstreamChannel("UNIT-GEN")
    assert channel_b.generation > channel_a.generation
    # 通道重建再 +1;不同 serial 互不影响
    before = _next_generation("UNIT-GEN2")
    assert _next_generation("UNIT-GEN2") == before + 1


def test_channel_stop_closes_subscribers():
    channel = UpstreamChannel("UNIT-STOP-SUB")
    sub = channel.subscribe_encoded()
    channel.stop()
    assert sub.closed
    assert sub.take(0.01) is None
    assert channel._subs == []


def test_subscribe_replays_config_and_session():
    channel = UpstreamChannel("UNIT-SUB")
    channel._enc_config = b"SPSPPS"
    channel._enc_session = (1080, 2400)
    sub = channel.subscribe_encoded()
    first = sub.take(0.01)
    second = sub.take(0.01)
    assert (first.kind, first.payload) == ("config", b"SPSPPS")
    assert (second.kind, second.width, second.height) == ("session", 1080, 2400)
    sub.close()
