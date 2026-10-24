"""pytest 失败证据钩子单元测试(工单 06):pytester 隔离运行。

子进程用 -p 显式加载插件:不依赖 editable 安装元数据中入口点的鲜度,
安装早于 pytest11 声明的环境也能稳定通过。
"""


pytest_plugins = ["pytester"]

_PLUGIN = "mobile_auto.evidence.pytest_plugin"

_CONFTEST = '''
import io

import pytest
from PIL import Image

from mobile_auto import DeviceSession, MobileConfig


class FakeBackend:
    def connect(self, serial):
        pass

    def close(self):
        pass

    def screen_size(self):
        return (1080, 2400)

    def screenshot_png(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 200, 10)).save(buffer, format="PNG")
        return buffer.getvalue()

    def dump_ui(self):
        return "<hierarchy/>" * 20


class NullLease:
    def __init__(self, serial):
        pass

    def acquire(self):
        pass

    def release(self):
        pass


@pytest.fixture
def mobile_session(request, tmp_path_factory):
    config = MobileConfig(
        device_serial="SERIAL1", artifacts_dir=tmp_path_factory.mktemp("art")
    )
    session = DeviceSession(
        config,
        backend_factory=lambda: FakeBackend(),
        device_enumerator=lambda: {"SERIAL1": "device"},
        lease_factory=lambda s: NullLease(s),
    )
    session.start()
    yield session
    session.close()
'''

_TEST_FAIL = '''
def test_failing(mobile_session):
    assert False, "故意失败"
'''


def _prepare(pytester):
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(_TEST_FAIL)


def test_flag_off_collects_nothing(pytester):
    _prepare(pytester)
    result = pytester.runpytest_subprocess("-p", _PLUGIN)
    result.assert_outcomes(failed=1)
    assert "mobile-auto-evidence" not in result.stdout.str()


def test_flag_on_writes_evidence_section(pytester):
    _prepare(pytester)
    result = pytester.runpytest_subprocess("-p", _PLUGIN, "--mobile-evidence", "-q")
    result.assert_outcomes(failed=1)
    out = result.stdout.str()
    assert "frame_source: u2" in out
    assert "failure-frame.png" in out


def test_flag_on_without_session_notes_missing(pytester):
    pytester.makepyfile(
        """
        def test_failing():
            assert False, "无会话"
        """
    )
    result = pytester.runpytest_subprocess("-p", _PLUGIN, "--mobile-evidence", "-q")
    result.assert_outcomes(failed=1)
    assert "未找到可用的 DeviceSession" in result.stdout.str()
