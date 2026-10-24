"""真机测试载具:--mobile-config 配置门控与失败取证钩子。

无配置或无设备时用例跳过,显示未执行、不计为通过(spec Testing Decisions)。
local.toml 为操作者本机配置,不入库;字段说明见 local.toml.example。
夹具负责点亮屏幕:设备空闲息屏是环境状态,不属于驱动契约。
用例失败时在会话关闭前收集失败证据(工单 05);取证错误不覆盖原始测试错误。
"""

from __future__ import annotations

import pytest

from mobile_auto import DeviceSession, MobileAdapter, load_config


def pytest_addoption(parser):
    parser.addoption(
        "--mobile-config",
        action="store",
        default=None,
        help="真机测试配置 TOML 路径;未提供时真机用例跳过",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture(scope="session")
def mobile_config(request):
    path = request.config.getoption("--mobile-config")
    if not path:
        pytest.skip("未提供 --mobile-config,真机用例未执行、不计为通过")
    return load_config(path)


def _wake_screen(serial: str) -> None:
    """点亮并上滑解锁(假定无密码锁屏,见 docs/demo-app.md 前置条件)。"""
    import uiautomator2 as u2

    device = u2.connect(serial)
    if not device.info.get("screenOn"):
        device.screen_on()
        device.swipe(0.5, 0.9, 0.5, 0.2, duration=0.2)


@pytest.fixture
def evidence(request):
    """记录操作时画面:测试中调用 evidence.record_frame(png_bytes, label)。

    画面在操作过程中采集,失败取证时与事后画面分开保存。
    """

    class _Recorder:
        def __init__(self):
            self.frames: list[tuple[str, bytes]] = []

        def record_frame(self, png: bytes, label: str) -> None:
            self.frames.append((label, png))

    recorder = _Recorder()
    request.node._evidence_recorder = recorder
    return recorder


@pytest.fixture
def mobile_session(mobile_config, request):
    """function 级设备会话;证据钩子(pytest_plugin)按名发现此 fixture。"""
    _wake_screen(mobile_config.device_serial)
    session = DeviceSession(mobile_config)
    session.start()
    yield session

    rep = getattr(request.node, "rep_call", None)
    failed = rep is not None and (
        rep.failed or (rep.outcome == "skipped" and hasattr(rep, "wasxfail"))
    )
    if failed:
        from mobile_auto.evidence.collector import collect_failure_evidence

        recorder = getattr(request.node, "_evidence_recorder", None)
        try:
            collect_failure_evidence(
                session,
                str(rep.longreprtext),
                operation_frames=tuple(recorder.frames) if recorder else (),
            )
        except Exception as exc:  # 取证失败不覆盖原始测试错误
            print(f"[evidence] 取证失败(原始测试错误不受影响):{exc!r}", flush=True)
    session.close()


@pytest.fixture
def adapter(mobile_session):
    return MobileAdapter(mobile_session)
