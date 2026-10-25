"""设备池 pytest fixture 单元测试(P2 工单 05):pytester 隔离子进程。

子进程注入伪造枚举/租约/后端,验证:池未配置显式报错、旗标抢设备建会话、
产物按 worker 分目录、运行状态文件落地、conftest 同名 fixture 可覆盖。
"""

from __future__ import annotations

import json

import pytest

pytest_plugins = ["pytester"]

_PLUGIN = "mobile_auto.evidence.pytest_plugin"

_FAKE_ENV = '''
import mobile_auto.pool as pool_mod
from mobile_auto.core import session as session_mod

STATES = {"S1": "device", "S2": "device"}


class FakeLease:
    def __init__(self, serial, worker=""):
        self.serial = serial
        self.holder = {"worker": worker}

    def acquire(self):
        pass

    def release(self):
        pass


class FakeBackend:
    def connect(self, serial):
        self.serial = serial

    def close(self):
        pass

    def screen_size(self):
        return (1080, 2400)

    def screenshot_png(self):
        return b"PNG-fake"


pool_mod.enumerate_device_states = lambda: dict(STATES)
pool_mod.DeviceLease = FakeLease
session_mod.enumerate_device_states = lambda: dict(STATES)
session_mod.U2Backend = FakeBackend
'''

_SINGLE_TEST = '''
def test_pool_session(mobile_session):
    assert mobile_session.serial in {"S1", "S2"}
'''


@pytest.fixture(autouse=True)
def _no_plugin_autoload(monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


def test_pool_fixture_fails_without_pool(pytester):
    pytester.makeconftest(_FAKE_ENV)
    pytester.makepyfile(_SINGLE_TEST)
    result = pytester.runpytest_subprocess("-p", _PLUGIN, "-q")
    result.assert_outcomes(errors=1)
    assert "需要设备池" in result.stdout.str()


def test_pool_fixture_acquires_and_closes(pytester, tmp_path):
    conftest = _FAKE_ENV + '''

import pytest

@pytest.fixture(autouse=True)
def _record(request):
    yield
    session = request.getfixturevalue("mobile_session")
    print("HELD-STATE", session.state.value)
'''
    pytester.makeconftest(conftest)
    pytester.makepyfile(_SINGLE_TEST)
    result = pytester.runpytest_subprocess(
        "-p", _PLUGIN, "-q", "--mobile-devices", "S1,S2", "-s"
    )
    result.assert_outcomes(passed=1)
    assert "HELD-STATE leased" in result.stdout.str()  # 用例期间会话持租约


def test_pool_lease_factory_reuses_prealoaded_lease(pytester):
    """会话经预获取租约启动:并发抢设备的窗口不存在(lease_factory 注入)。"""
    conftest = _FAKE_ENV + '''

import mobile_auto.core.session as sm

created = []

class RecordingSession(sm.DeviceSession):
    def __init__(self, config, **kw):
        created.append(kw.get("lease_factory"))
        super().__init__(config, **kw)

sm.DeviceSession = RecordingSession
'''
    pytester.makeconftest(conftest)
    pytester.makepyfile(_SINGLE_TEST)
    result = pytester.runpytest_subprocess(
        "-p", _PLUGIN, "-q", "--mobile-devices", "S1"
    )
    result.assert_outcomes(passed=1)


def test_pool_adapter_fixture_wraps_session(pytester):
    conftest = _FAKE_ENV
    pytester.makeconftest(conftest)
    pytester.makepyfile(
        """
        import asyncio

        def test_adapter(mobile_adapter):
            png = asyncio.run(mobile_adapter.screenshot())
            assert png == b"PNG-fake"
        """
    )
    result = pytester.runpytest_subprocess(
        "-p", _PLUGIN, "-q", "--mobile-devices", "S1"
    )
    result.assert_outcomes(passed=1)


def test_run_state_written_during_pool_run(pytester, tmp_path):
    pytester.makeconftest(_FAKE_ENV)
    pytester.makepyfile(_SINGLE_TEST)
    result = pytester.runpytest_subprocess(
        "-p", _PLUGIN, "-q", "--mobile-devices", "S1"
    )
    result.assert_outcomes(passed=1)
    # load_config(None) 的 artifacts 基于子进程 cwd(pytester 临时目录)
    state_file = pytester.path / "artifacts" / "runs" / "main.json"
    assert state_file.is_file(), f"状态文件未落地:{state_file}"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["phase"] == "finished"
    assert state["serial"] == "S1"


def test_conftest_fixture_overrides_plugin(pytester):
    """conftest 同名 mobile_session 覆盖插件池化版本(内网绑定层路径)。"""
    override = _FAKE_ENV + '''

import pytest


@pytest.fixture(scope="session")
def mobile_session():
    return "CONFTEST-SESSION"
'''
    pytester.makeconftest(override)
    pytester.makepyfile(
        """
        def test_override(mobile_session):
            assert mobile_session == "CONFTEST-SESSION"
        """
    )
    result = pytester.runpytest_subprocess(
        "-p", _PLUGIN, "-q", "--mobile-devices", "S1"
    )
    result.assert_outcomes(passed=1)
