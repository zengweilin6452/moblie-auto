"""pytest --debug-viewer 后台调试服务(P2 工单 03)。

controller 进程内以守护线程运行 viewer:复用 serve 的应用与设备注册表,
端口默认取配置 port,被占则递增探测空闲(上限 20);停机经 uvicorn
should_exit 优雅退出后关停设备注册表。仅监听回环;用例执行不依赖本服务
(PRD §7.1),测试运行中预览读不占租约。缺 viewer extra(fastapi/uvicorn)
时如实报配置错误,不静默跳过。
"""

from __future__ import annotations

import socket
import threading
import time

from ..config import MobileConfig
from ..errors import ConfigurationError

PORT_PROBE_LIMIT = 20
_STARTUP_TIMEOUT_SECONDS = 15.0


def _find_free_port(start: int) -> int | None:
    # 不设 SO_REUSEADDR:Windows 上它会连"他人监听中"的端口也绑定成功,
    # 探测就失去意义(单测实证);默认 bind 被占即 OSError 才是所需语义
    for port in range(start, start + PORT_PROBE_LIMIT):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


class DebugViewerHandle:
    """后台 viewer 的句柄:url 供打印,stop 优雅停机并关停注册表。"""

    def __init__(self, thread, server, registry, url) -> None:
        self._thread = thread
        self._server = server
        self._registry = registry
        self.url = url

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout)
        self._registry.shutdown()  # 停 scrcpy 通道与会话(工单 01 语义)


def start_debug_viewer(config: MobileConfig) -> DebugViewerHandle:
    """在后台线程启动 viewer;端口探测失败或缺依赖报 ConfigurationError。"""
    try:
        import uvicorn
    except ImportError as exc:  # viewer extra 未安装
        raise ConfigurationError(
            f"--debug-viewer 需要 viewer extra(fastapi/uvicorn),当前未安装:{exc}"
        ) from exc
    from .app import _DeviceRegistry, create_app

    port = _find_free_port(config.port)
    if port is None:
        raise ConfigurationError(
            f"viewer 端口 {config.port}-{config.port + PORT_PROBE_LIMIT - 1} 均被占用"
        )
    registry = _DeviceRegistry(config)  # 会话在首次被寻址时才连接设备
    app = create_app(registry=registry)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(
        target=server.run, name="mobile-auto-debug-viewer", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not server.started:
        if not thread.is_alive():
            raise ConfigurationError("viewer 后台线程启动即退出(详见设备与端口状态)")
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        raise ConfigurationError("viewer 启动超时")
    return DebugViewerHandle(thread, server, registry, f"http://127.0.0.1:{port}")
