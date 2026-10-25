"""stream_backend 配置与通道工厂单元测试(SU-02)。"""

import pytest

from mobile_auto import MobileConfig
from mobile_auto.stream import channel_factory as make_factory


def test_config_default_and_validation():
    assert MobileConfig().stream_backend == "wetest"
    with pytest.raises(Exception, match="stream_backend"):
        MobileConfig(stream_backend="h264")
    config = MobileConfig(stream_backend="upstream")
    assert config.stream_backend == "upstream"


def test_config_toml_roundtrip(tmp_path):
    from mobile_auto.config import load_config

    toml = tmp_path / "local.toml"
    toml.write_text('stream_backend = "upstream"\n', encoding="utf-8")
    assert load_config(toml).stream_backend == "upstream"


def test_factory_selects_backend_class():
    from mobile_auto.stream.upstream import UpstreamChannel

    factory = make_factory("upstream")
    assert factory is UpstreamChannel
    from mobile_auto.stream.channel import ScrcpyChannel

    wetest = make_factory("wetest")
    assert wetest is ScrcpyChannel
    channel = wetest("SERIAL")  # 构造无副作用
    assert channel._serial == "SERIAL"


def test_factory_override_wins():
    marker = object()
    factory = make_factory("wetest", channel_factory_override=lambda s: marker)
    assert factory("S") is marker


def test_factory_rejects_unknown_backend():
    from mobile_auto import ConfigurationError

    with pytest.raises(ConfigurationError, match="stream_backend"):
        make_factory("nope")


def test_session_uses_backend_from_config():
    """DeviceSession 驱动通道经工厂按 config 选择(注入探针验证)。"""
    from fakes import FakeBackend, make_session

    from mobile_auto import MobileConfig

    created = []

    def probe_factory(serial):
        channel = make_factory("upstream")
        instance = channel(serial)
        created.append(type(instance).__name__)
        raise RuntimeError("probe: stop before start")

    session, backend = make_session(
        MobileConfig(device_serial="SERIAL1", stream_backend="upstream"),
        backend=FakeBackend(),
        channel_factory=probe_factory,
    )
    session.start()
    try:
        future = session.submit(lambda backend: session.get_or_start_channel())
        try:
            future.result(timeout=5)
        except RuntimeError as exc:
            assert "probe" in str(exc)
        assert created == ["UpstreamChannel"]
    finally:
        session.close()


def test_viewer_manager_uses_backend_from_config():
    """viewer _ChannelManager 同样经工厂按 config 选择。"""
    from fakes import FakeBackend, make_session

    from mobile_auto import MobileConfig
    from mobile_auto.viewer.app import _ChannelManager

    created = []

    def probe_factory(serial):
        created.append(serial)
        raise RuntimeError("probe")

    session, backend = make_session(
        MobileConfig(device_serial="SERIAL1", stream_backend="upstream"),
        backend=FakeBackend(),
    )
    session.start()
    session.release_lease()
    manager = _ChannelManager(session, channel_factory=probe_factory)
    assert manager.get() is None  # 启动失败按退避返回 None
    assert created == ["SERIAL1"]
    manager.shutdown()
    session.close()
