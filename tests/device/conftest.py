"""真机测试载具:--mobile-config 配置门控。

无配置或无设备时用例跳过,显示未执行、不计为通过(spec Testing Decisions)。
local.toml 为操作者本机配置,不入库;字段说明见 local.toml.example。
夹具负责点亮屏幕:设备空闲息屏是环境状态,不属于驱动契约。
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
def adapter(mobile_config):
    _wake_screen(mobile_config.device_serial)
    session = DeviceSession(mobile_config)
    session.start()
    yield MobileAdapter(session)
    session.close()
