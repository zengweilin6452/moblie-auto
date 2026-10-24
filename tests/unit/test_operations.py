"""工单 03 新增操作的外部行为:输入、属性、按键、滚动、就绪等待与句柄重用。"""

import pytest
from fakes import FakeBackend, FakeElement, make_adapter

from mobile_auto import (
    ConfigurationError,
    InvalidTarget,
    Locator,
    MobileConfig,
    StaleHandle,
    Target,
    WaitTimeout,
)

SMALL_BUDGET = MobileConfig(device_serial="SERIAL1", wait_timeout=0.2, poll_interval=0.05)


def edit_box(text=""):
    return FakeElement(
        (0, 0, 500, 100),
        text=text,
        info={"className": "android.widget.EditText", "resourceName": "com.demo:id/input"},
    )


async def test_input_text_clears_then_replaces():
    box = edit_box(text="旧文本")
    adapter, _ = make_adapter(
        config=SMALL_BUDGET,
        backend=FakeBackend(elements={("resource_id", "com.demo:id/input"): [box]}),
    )
    await adapter.input_text(
        Target(Locator.RESOURCE_ID, "com.demo:id/input"), "新文本"
    )
    assert box.text_inputs == ["", "新文本"]


async def test_input_text_rejects_non_editable_target():
    label = FakeElement((0, 0, 100, 50), text="标签")
    adapter, _ = make_adapter(
        config=SMALL_BUDGET,
        backend=FakeBackend(elements={("text", "标签"): [label]}),
    )
    with pytest.raises(InvalidTarget, match="不可编辑"):
        await adapter.input_text(Target(Locator.TEXT, "标签"), "x")
    assert label.text_inputs == []


async def test_input_text_times_out_with_element_not_found():
    from mobile_auto import ElementNotFound

    adapter, _ = make_adapter(config=SMALL_BUDGET)
    with pytest.raises(ElementNotFound):
        await adapter.input_text(Target(Locator.TEXT, "永不出现"), "x")


async def test_get_attribute_known_missing_and_unknown():
    row = FakeElement(
        (0, 0, 100, 50),
        text="WLAN",
        info={
            "className": "android.widget.TextView",
            "resourceName": "com.demo:id/row",
            "enabled": True,
            "clickable": False,
            "contentDescription": "",
            "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 50},
        },
    )
    adapter, _ = make_adapter(
        backend=FakeBackend(elements={("text", "WLAN"): [row]}),
        config=SMALL_BUDGET,
    )
    target = Target(Locator.TEXT, "WLAN")
    assert await adapter.get_attribute(target, "text") == "WLAN"
    assert await adapter.get_attribute(target, "resourceId") == "com.demo:id/row"
    assert await adapter.get_attribute(target, "enabled") == "true"
    assert await adapter.get_attribute(target, "clickable") == "false"
    assert await adapter.get_attribute(target, "contentDescription") == ""  # 已知但缺失
    assert await adapter.get_attribute(target, "bounds") == (0, 0, 100, 50)
    with pytest.raises(InvalidTarget, match="未知属性"):
        await adapter.get_attribute(target, "password")


async def test_press_key_limited_table_and_back():
    adapter, backend = make_adapter()
    await adapter.back()
    await adapter.press_key("home")
    await adapter.press_key("delete")
    assert backend.press_keys == ["back", "home", "delete"]
    with pytest.raises(InvalidTarget, match="不支持的按键"):
        await adapter.press_key("volume_up")
    with pytest.raises(InvalidTarget, match="不支持的按键"):
        await adapter.press_key("shell rm -rf /")


async def test_scroll_direction_semantics_and_clamping():
    adapter, backend = make_adapter()
    await adapter.scroll("down", 500)  # 手指向上
    sx, sy, ex, ey, duration = backend.swipes[-1]
    assert sy > ey and sx == ex and duration == pytest.approx(0.3)
    await adapter.scroll("up", 10**9)  # 超限夹限到安全区域
    sx, sy, ex, ey, _ = backend.swipes[-1]
    assert 0 < sy < 2400 and 0 < ey < 2400
    await adapter.scroll("left", 100)  # 手指向右
    sx, sy, ex, ey, _ = backend.swipes[-1]
    assert sx < ex
    with pytest.raises(InvalidTarget, match="方向"):
        await adapter.scroll("diagonal", 100)
    with pytest.raises(InvalidTarget, match="正整数"):
        await adapter.scroll("down", -5)
    with pytest.raises(InvalidTarget, match="正整数"):
        await adapter.scroll("down", True)


async def test_wait_for_load_uses_configured_ready_target():
    config = MobileConfig(
        device_serial="SERIAL1",
        launch_ready_target=Target(Locator.TEXT, "已就绪"),
        poll_interval=0.02,
    )
    adapter, _ = make_adapter(
        config=config,
        backend=FakeBackend(
            find_script=[[], []],
            elements={("text", "已就绪"): [FakeElement((0, 0, 10, 10), text="已就绪")]},
        ),
    )
    await adapter.wait_for_load()  # 第三轮可见,不抛错


async def test_wait_for_load_requires_configuration():
    adapter, _ = make_adapter()  # 默认无 launch_ready_target
    with pytest.raises(ConfigurationError, match="launch_ready_target"):
        await adapter.wait_for_load()


async def test_wait_for_load_raises_wait_timeout():
    config = MobileConfig(
        device_serial="SERIAL1",
        launch_ready_target=Target(Locator.TEXT, "永不来"),
        wait_timeout=0.15,
        poll_interval=0.05,
    )
    adapter, _ = make_adapter(config=config)
    with pytest.raises(WaitTimeout):
        await adapter.wait_for_load()


async def test_find_elements_handles_carry_identity_and_owner():
    row = FakeElement(
        (0, 0, 100, 50),
        text="WLAN",
        info={
            "className": "android.widget.TextView",
            "resourceName": "com.demo:id/row",
            "contentDescription": "无线网络",
        },
    )
    adapter, _ = make_adapter(
        backend=FakeBackend(elements={("text", "WLAN"): [row]}),
        config=SMALL_BUDGET,
    )
    (handle,) = await adapter.find_elements(Target(Locator.TEXT, "WLAN"))
    assert handle.identity == (
        "android.widget.TextView",
        "com.demo:id/row",
        "无线网络",
    )
    assert handle.owner is not None


async def test_handle_get_text_and_click_roundtrip():
    row = FakeElement(
        (0, 0, 100, 50),
        text="WLAN",
        info={"className": "android.widget.TextView"},
    )
    adapter, _ = make_adapter(
        backend=FakeBackend(elements={("text", "WLAN"): [row]}),
        config=SMALL_BUDGET,
    )
    (handle,) = await adapter.find_elements(Target(Locator.TEXT, "WLAN"))
    assert await handle.get_text() == "WLAN"
    await handle.click()
    assert row.click_calls == 1


async def test_stale_handle_when_attributes_change():
    row = FakeElement(
        (0, 0, 100, 50),
        text="WLAN",
        info={"className": "android.widget.TextView", "resourceName": "com.demo:id/row"},
    )
    adapter, backend = make_adapter(
        backend=FakeBackend(elements={("text", "WLAN"): [row]}),
        config=SMALL_BUDGET,
    )
    (handle,) = await adapter.find_elements(Target(Locator.TEXT, "WLAN"))
    backend.elements[("text", "WLAN")][0].info["resourceName"] = "com.demo:id/other"
    with pytest.raises(StaleHandle, match="关键属性"):
        await handle.get_text()
    with pytest.raises(StaleHandle):
        await handle.click()


async def test_stale_handle_when_target_disappears():
    row = FakeElement(
        (0, 0, 100, 50),
        text="WLAN",
        info={"className": "android.widget.TextView"},
    )
    adapter, backend = make_adapter(
        backend=FakeBackend(elements={("text", "WLAN"): [row]}),
        config=SMALL_BUDGET,
    )
    (handle,) = await adapter.find_elements(Target(Locator.TEXT, "WLAN"))
    backend.elements[("text", "WLAN")].clear()
    with pytest.raises(StaleHandle, match="无法再次唯一确认"):
        await handle.get_text()


async def test_handle_rejected_when_ambiguous():
    rows = [
        FakeElement((0, 0, 100, 50), info={"className": "android.widget.TextView"}),
        FakeElement((0, 60, 100, 110), info={"className": "android.widget.TextView"}),
    ]
    adapter, _ = make_adapter(
        backend=FakeBackend(elements={("text", "WLAN"): rows}),
        config=SMALL_BUDGET,
    )
    target = Target(Locator.TEXT, "WLAN")
    handles = await adapter.find_elements(target)
    assert len(handles) == 2
    # 两条同文本且无序号:句柄重用遇到歧义 → 句柄过期,不指向任意节点
    with pytest.raises(StaleHandle):
        await handles[0].get_text()
