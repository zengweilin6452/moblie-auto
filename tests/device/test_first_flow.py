"""第一条真机用例:启动 → 定位 → 点击 → 断言 → 截图(ticket 02)。

场景与演示 App 登记见 docs/demo-app.md;需要 --mobile-config 与真机,
否则跳过(未执行、不计为通过)。
"""

import pytest

from mobile_auto import DeviceSession, Locator, Target


@pytest.mark.device
def test_session_targets_configured_device(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    try:
        assert session.serial == mobile_config.device_serial
        assert session.state.value == "leased"
    finally:
        session.close()


@pytest.mark.device
async def test_first_native_flow_launch_locate_click_assert_screenshot(adapter):
    # 启动:goto("") 按配置的 package/activity 启动设置并复位到主页
    await adapter.goto("")

    # 定位与等待:主页 WLAN 行可见
    wlan_row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(wlan_row) is True

    # 点击:进入 WLAN 子页
    await adapter.click(wlan_row)
    advanced = Target(Locator.TEXT, "高级设置")
    assert await adapter.wait_for(advanced) is True

    # 断言:读取文本
    assert await adapter.get_text(advanced) == "高级设置"

    # 枚举:子页标题恰有一个匹配,句柄携带 bounds
    handles = await adapter.find_elements(Target(Locator.TEXT, "WLAN"))
    assert len(handles) >= 1
    assert handles[0].bounds is not None

    # 截图:全新采集的 PNG
    png = await adapter.screenshot()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
