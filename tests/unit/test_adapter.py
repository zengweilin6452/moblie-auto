"""MobileAdapter 异步公共接口:注入伪造后端驱动定位、点击、等待与截图。"""

import asyncio

import pytest
from fakes import FakeBackend, FakeElement, make_adapter

from mobile_auto import (
    AmbiguousTarget,
    ConfigurationError,
    DeviceUnavailable,
    ElementNotFound,
    Locator,
    MobileConfig,
    SessionFaulted,
    Target,
    UnsupportedOperation,
)


def backend_with(groups: dict) -> FakeBackend:
    """groups: {(Locator 值, value): [FakeElement, ...]}"""
    return FakeBackend(elements=groups)


async def test_platform_is_android():
    adapter, _ = make_adapter()
    assert adapter.platform == "android"


async def test_find_elements_returns_all_visible_in_order():
    backend = backend_with(
        {
            ("resource_id", "com.demo:id/row"): [
                FakeElement((0, 0, 1080, 100), text="第一行"),
                FakeElement((0, 100, 1080, 200), text="第二行"),
                FakeElement(None, text="无边界"),
            ]
        }
    )
    adapter, _ = make_adapter(backend=backend)
    handles = await adapter.find_elements(
        Target(Locator.RESOURCE_ID, "com.demo:id/row")
    )
    assert [h.text for h in handles] == ["第一行", "第二行"]
    assert handles[0].bounds == (0, 0, 1080, 100)


async def test_find_elements_with_index_returns_at_most_one():
    backend = backend_with(
        {("text", "行"): [FakeElement((0, 0, 10, 10)), FakeElement((0, 20, 10, 30))]}
    )
    adapter, _ = make_adapter(backend=backend)
    target = Target(Locator.TEXT, "行")
    assert len(await adapter.find_elements(target)) == 2
    indexed = await adapter.find_elements(Target(Locator.TEXT, "行", index=1))
    assert len(indexed) == 1 and indexed[0].bounds == (0, 20, 10, 30)
    assert await adapter.find_elements(Target(Locator.TEXT, "行", index=9)) == []


async def test_click_unique_element():
    elements = [FakeElement((0, 0, 10, 10))]
    adapter, _ = make_adapter(backend=backend_with({("text", "登录"): elements}))
    await adapter.click(Target(Locator.TEXT, "登录"))
    assert elements[0].click_calls == 1


async def test_click_errors_are_distinguished():
    single = [FakeElement((0, 0, 10, 10))]
    multiple = single + [FakeElement((0, 20, 10, 30))]
    config = MobileConfig(device_serial="SERIAL1", wait_timeout=0.2, poll_interval=0.05)
    adapter, backend = make_adapter(
        config=config,
        backend=backend_with({("text", "唯一"): single, ("text", "多"): multiple}),
    )
    with pytest.raises(ElementNotFound):
        await adapter.click(Target(Locator.TEXT, "不存在"))
    assert backend.find_calls >= 2  # 预算内轮询,不是单次查询
    queries_before = backend.find_calls
    with pytest.raises(AmbiguousTarget):
        await adapter.click(Target(Locator.TEXT, "多"))
    assert backend.find_calls == queries_before + 1  # 歧义立即报,不重试
    await adapter.click(Target(Locator.TEXT, "多", index=1))
    assert multiple[1].click_calls == 1
    assert single[0].click_calls == 0


async def test_click_and_get_text_wait_for_late_element():
    late = [FakeElement((0, 0, 10, 10), text="稍后出现")]
    backend = FakeBackend(
        find_script=[[], []],
        elements={("text", "稍后出现"): late},
    )
    config = MobileConfig(device_serial="SERIAL1", poll_interval=0.02)
    adapter, _ = make_adapter(config=config, backend=backend)
    await adapter.click(Target(Locator.TEXT, "稍后出现"))
    assert late[0].click_calls == 1
    backend.find_script = [[], []]
    assert await adapter.get_text(Target(Locator.TEXT, "稍后出现")) == "稍后出现"


async def test_get_text_unique_empty_and_ambiguous():
    adapter, _ = make_adapter(
        backend=backend_with(
            {
                ("text", "标题"): [FakeElement((0, 0, 10, 10), text="设置")],
                ("text", "空文本"): [FakeElement((0, 0, 10, 10), text="")],
                ("text", "重复"): [
                    FakeElement((0, 0, 10, 10)),
                    FakeElement((0, 20, 10, 30)),
                ],
            }
        )
    )
    assert await adapter.get_text(Target(Locator.TEXT, "标题")) == "设置"
    assert await adapter.get_text(Target(Locator.TEXT, "空文本")) == ""
    with pytest.raises(AmbiguousTarget):
        await adapter.get_text(Target(Locator.TEXT, "重复"))


async def test_wait_for_found_immediately_and_missing_after_budget():
    adapter, _ = make_adapter(
        backend=backend_with({("text", "在"): [FakeElement((0, 0, 10, 10))]})
    )
    assert await adapter.wait_for(Target(Locator.TEXT, "在")) is True
    missing = Target(Locator.TEXT, "不在")
    assert await adapter.wait_for(missing, timeout=0.15) is False
    assert await adapter.wait_for(missing, timeout=0) is False


async def test_wait_for_zero_timeout_single_query():
    adapter, backend = make_adapter(
        backend=backend_with({("text", "在"): [FakeElement((0, 0, 10, 10))]})
    )
    assert await adapter.wait_for(Target(Locator.TEXT, "在"), timeout=0) is True
    queries_after_found = backend.find_calls
    assert await adapter.wait_for(Target(Locator.TEXT, "不在"), timeout=0) is False
    assert backend.find_calls == queries_after_found + 1


async def test_wait_for_element_appearing_later():
    backend = FakeBackend(
        find_script=[[], [], [FakeElement((0, 0, 10, 10))]],
        elements={("text", "稍后"): [FakeElement((0, 0, 10, 10))]},
    )
    config = MobileConfig(device_serial="SERIAL1", poll_interval=0.02)
    adapter, _ = make_adapter(config=config, backend=backend)
    assert await adapter.wait_for(Target(Locator.TEXT, "稍后")) is True


async def test_wait_for_rejects_invalid_timeouts():
    adapter, _ = make_adapter()
    target = Target(Locator.TEXT, "x")
    with pytest.raises(ValueError):
        await adapter.wait_for(target, timeout=-1)
    with pytest.raises(ValueError):
        await adapter.wait_for(target, timeout=float("nan"))


async def test_wait_for_propagates_device_errors_not_false():
    adapter, backend = make_adapter()

    def broken_find(by, value):
        raise DeviceUnavailable("设备掉线")

    backend.find_elements = broken_find
    with pytest.raises(DeviceUnavailable):
        await adapter.wait_for(Target(Locator.TEXT, "x"), timeout=0.1)


async def test_wait_for_cancellation_stops_new_polls():
    config = MobileConfig(device_serial="SERIAL1", poll_interval=0.05)
    adapter, backend = make_adapter(config=config)  # 目标永不出现
    task = asyncio.ensure_future(
        adapter.wait_for(Target(Locator.TEXT, "幽灵"), timeout=30)
    )
    await asyncio.sleep(0.08)
    assert backend.find_calls >= 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)  # 等在途查询结束
    settled = backend.find_calls
    await asyncio.sleep(0.3)  # 多倍轮询间隔,若仍在轮询必然增长
    assert backend.find_calls == settled


async def test_screenshot_returns_fresh_png_bytes_each_time():
    adapter, backend = make_adapter()
    first = await adapter.screenshot()
    second = await adapter.screenshot()
    assert first == b"PNG-1" and second == b"PNG-2"
    assert backend.screenshot_calls == 2


async def test_goto_launches_configured_app_and_validates():
    config = MobileConfig(
        device_serial="SERIAL1", app_package="com.demo", app_activity=".MainActivity"
    )
    adapter, backend = make_adapter(config=config)
    await adapter.goto("")
    assert backend.app_starts == [("com.demo", ".MainActivity", True)]


async def test_goto_deeplink_follows_whitelist():
    config = MobileConfig(
        device_serial="SERIAL1", app_package="com.demo", allowed_deeplink_schemes=("demoapp",)
    )
    adapter, _ = make_adapter(config=config)
    # 白名单为空:任何链接立即报配置错误(plan 5.3)
    with pytest.raises(ConfigurationError, match="白名单"):
        await adapter.goto("https://example.com")
    # 在白名单内但启动能力未交付:明确报不支持
    with pytest.raises(UnsupportedOperation, match="工单"):
        await adapter.goto("demoapp://page")
    # 白名单外的 scheme:即使配置了其他白名单项也报配置错误
    with pytest.raises(ConfigurationError):
        await adapter.goto("https://example.com")


async def test_goto_without_app_package_is_configuration_error():
    adapter, _ = make_adapter()  # 默认配置无 app_package
    with pytest.raises(ConfigurationError):
        await adapter.goto("")


async def test_unsupported_locator_modes_reported_not_silent():
    adapter, _ = make_adapter()
    for by in (Locator.IMAGE, Locator.XPATH, Locator.U2_SELECTOR):
        with pytest.raises(UnsupportedOperation):
            await adapter.click(Target(by, "x"))


async def test_adapter_close_idempotent_then_rejected():
    adapter, _ = make_adapter()
    await adapter.close()
    await adapter.close()
    with pytest.raises(SessionFaulted, match="已关闭"):
        await adapter.screenshot()
