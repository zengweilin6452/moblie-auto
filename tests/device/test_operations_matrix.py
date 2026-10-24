"""真机操作矩阵(工单 03):五种定位、输入、属性、按键、滚动、句柄过期。

场景基于演示 App(系统设置)登记的稳定元素,见 docs/demo-app.md;
需要 --mobile-config 与真机,否则跳过(未执行、不计为通过)。
注意:搜索等操作会在页面留下残留文本,需要干净主页的断言都放在
冷启动 goto("") 之后、搜索流之前执行。
"""

import pytest

from mobile_auto import (
    AmbiguousTarget,
    DeviceSession,
    InvalidTarget,
    Locator,
    StaleHandle,
    Target,
)
from mobile_auto.core.ui_tree import flatten, parse_ui_hierarchy


@pytest.mark.device
async def test_native_operations_matrix(adapter):
    # 启动(冷启动保证主页顶部、无搜索残留)
    await adapter.goto("")

    # ── 五种原生定位描述 ──────────────────────────────────────
    wlan = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(wlan) is True
    assert await adapter.is_visible(Target(Locator.U2_SELECTOR, '{"text": "WLAN"}')) is True
    assert await adapter.is_visible(Target(Locator.XPATH, '//*[@text="WLAN"]')) is True
    assert await adapter.is_visible(Target(Locator.ACCESSIBILITY_ID, "搜索")) is True
    # resource_id 主页多行同 id(android:id/title):有序号返回至多一个
    handles = await adapter.find_elements(
        Target(Locator.RESOURCE_ID, "android:id/title", index=0)
    )
    assert len(handles) == 1

    # ── 歧义与错误分类 ────────────────────────────────────────
    with pytest.raises(AmbiguousTarget):
        await adapter.click(Target(Locator.RESOURCE_ID, "android:id/title"))
    with pytest.raises(InvalidTarget, match="不可编辑"):
        await adapter.input_text(Target(Locator.TEXT, "WLAN"), "x")

    # ── 属性读取:布尔字符串与四整数 bounds ───────────────────
    assert await adapter.get_attribute(wlan, "enabled") == "true"
    bounds = await adapter.get_attribute(wlan, "bounds")
    assert isinstance(bounds, tuple) and len(bounds) == 4
    assert all(isinstance(v, int) for v in bounds)
    assert await adapter.get_attribute(wlan, "contentDescription") == ""  # 已知缺失

    # ── 句柄重用:目标消失后报句柄过期,不指向新节点 ─────────
    # (主/子页标题的 class+resourceId 相同,原生句柄身份不含位置;
    #  用仅存在于子页的"高级设置"演示目标消失的过期路径)
    await adapter.click(wlan)  # 进入 WLAN 子页
    advanced = Target(Locator.TEXT, "高级设置")
    assert await adapter.wait_for(advanced) is True
    (sub_handle,) = await adapter.find_elements(advanced)
    await adapter.back()
    assert await adapter.wait_for(wlan) is True
    with pytest.raises(StaleHandle):
        await sub_handle.get_text()

    # ── 滚动:down = 内容上移,列表底部项可见 ─────────────────
    await adapter.scroll("down", 1200)
    assert await adapter.wait_for(Target(Locator.TEXT, "桌面")) is True

    # ── 按键:home 离开应用 ───────────────────────────────────
    await adapter.press_key("home")

    # ── 输入:冷启动后进搜索框,清空并替换(最后执行,避免残留) ──
    await adapter.goto("")
    assert await adapter.wait_for(Target(Locator.ACCESSIBILITY_ID, "搜索")) is True
    await adapter.click(Target(Locator.ACCESSIBILITY_ID, "搜索"))
    search_box = Target(Locator.RESOURCE_ID, "android:id/input")
    await adapter.input_text(search_box, "蓝牙")
    assert await adapter.get_text(search_box) == "蓝牙"
    await adapter.input_text(search_box, "WLAN")
    assert await adapter.get_text(search_box) == "WLAN"  # 替换而非追加
    await adapter.back()


@pytest.mark.device
def test_ui_tree_dump_and_normalize(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    try:
        xml = session.submit(lambda backend: backend.dump_ui()).result(timeout=15)
    finally:
        session.close()
    root = parse_ui_hierarchy(xml)
    texts = {node.text for node in flatten(root)}
    assert "设置" in texts or "WLAN" in texts  # 设置页面已加载
