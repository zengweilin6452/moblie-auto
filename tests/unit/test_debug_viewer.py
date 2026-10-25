"""--debug-viewer 后台服务单元测试(P2 工单 03):真实端口起停与门控。"""

from __future__ import annotations

import socket

import httpx
import pytest

from mobile_auto import ConfigurationError, MobileConfig
from mobile_auto.viewer.server import _find_free_port, start_debug_viewer

pytest.importorskip("fastapi", reason="viewer extra 未安装")

pytest_plugins = ["pytester"]


def _free_base() -> int:
    port = _find_free_port(18790)
    assert port is not None
    return port


def test_start_serves_status_then_stop_closes_port():
    handle = start_debug_viewer(MobileConfig(port=_free_base()))
    try:
        response = httpx.get(f"{handle.url}/api/v1/status", timeout=10)
        assert response.status_code == 200
        assert response.json()["service_version"]
    finally:
        handle.stop()
    assert not handle._thread.is_alive()
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"{handle.url}/api/v1/status", timeout=2)


def test_find_free_port_skips_occupied():
    base = _free_base()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", base))
    blocker.listen(1)
    try:
        assert _find_free_port(base) == base + 1
    finally:
        blocker.close()


def test_missing_viewer_extra_raises_configuration_error(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "uvicorn", None)  # import 即 ImportError
    with pytest.raises(ConfigurationError, match="viewer extra"):
        start_debug_viewer(MobileConfig(port=_free_base()))


# ── 插件门控(pytester 子进程)────────────────────────────


_PLUGIN = "mobile_auto.evidence.pytest_plugin"


@pytest.fixture(autouse=True)
def _no_plugin_autoload(monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


def test_flag_off_starts_no_viewer(pytester):
    pytester.makepyfile("def test_ok():\n    assert True")
    result = pytester.runpytest_subprocess("-p", _PLUGIN, "-q")
    result.assert_outcomes(passed=1)
    assert "mobile-auto viewer" not in result.stdout.str()


def test_flag_on_prints_url_and_passes(pytester):
    pytester.makepyfile("def test_ok():\n    assert True")
    result = pytester.runpytest_subprocess("-p", _PLUGIN, "-q", "--debug-viewer")
    result.assert_outcomes(passed=1)
    assert "mobile-auto viewer 运行中:http://127.0.0.1:" in result.stdout.str()


def test_worker_process_does_not_start_viewer(pytester, monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    pytester.makepyfile("def test_ok():\n    assert True")
    result = pytester.runpytest_subprocess("-p", _PLUGIN, "-q", "--debug-viewer")
    result.assert_outcomes(passed=1)
    assert "mobile-auto viewer" not in result.stdout.str()
