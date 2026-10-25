"""scrcpy 视频流/控制通道(P1)与随包 server 资产。

后端选择(P2 上游迁移 SU-02):stream_backend 决定通道实现——wetest
(内嵌协议 fork,Android<17)/ upstream(官方 server v5.0.1,Android 17
兼容)。两实现同接口;显式传入的 channel_factory(测试注入)优先于配置。
"""

from __future__ import annotations

from ..errors import ConfigurationError

_BACKENDS = ("wetest", "upstream")


def channel_factory(backend: str = "wetest", *, channel_factory_override=None):
    """返回 serial → 通道实例 的工厂;override 优先(测试/绑定层注入)。"""
    if channel_factory_override is not None:
        return channel_factory_override
    if backend == "upstream":
        from .upstream import UpstreamChannel

        return UpstreamChannel
    if backend == "wetest":
        from .channel import ScrcpyChannel

        return ScrcpyChannel
    raise ConfigurationError(
        f"stream_backend 必须是 {' / '.join(_BACKENDS)} 之一:{backend!r}"
    )


__all__ = ["channel_factory"]
