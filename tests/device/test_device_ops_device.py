"""adapter.device 手势扩展面真机验证(演示 App = 系统设置 + 桌面长按)。

覆盖:
- 三原语(tap/swipe/long_press)auto 路径走 scrcpy 且行为生效;
- source 钉死语义(u2 强制、scrcpy 通道确实在用);
- 密集滑动序列 u2 vs scrcpy 墙钟对比(数字打印供 validation.md 引用,
  断言只要求 scrcpy 不慢于 u2,不做固定时间断言);
- 长按行为判据:MIUI 桌面空白处长按进入编辑模式(小部件标记,
  2026-10-25 探针同款判据)。
"""

import asyncio
import time

import pytest

from mobile_auto import Locator, Target

MENU_MARK = "小部件"


async def _dump(adapter) -> str:
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: adapter._session.submit(lambda b: b.dump_ui()).result(25)
    )


@pytest.mark.device
async def test_device_tap_swipe_longpress(adapter, mobile_config):
    # ── tap:坐标点击 WLAN 行中心(auto → scrcpy)──────────────
    await adapter.goto("")
    wlan = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(wlan)
    (row,) = await adapter.find_elements(wlan)
    left, top, right, bottom = row.bounds
    cx, cy = (left + right) // 2, (top + bottom) // 2
    source = await adapter.device.tap(cx, cy)
    assert source == "scrcpy", "auto 首选应命中 scrcpy 通道"
    assert await adapter.wait_for(Target(Locator.TEXT, "网络加速")), "tap 未进入 WLAN 子页"
    await adapter.back()

    # ── swipe:坐标滑动列表到底部(契约 scroll 同款判据)───────
    await adapter.goto("")
    assert await adapter.wait_for(wlan)
    source = await adapter.device.swipe(540, 1900, 540, 700, 0.3)
    assert source == "scrcpy"
    assert await adapter.wait_for(Target(Locator.TEXT, "桌面")), "滑动未到列表底部"
    await adapter.device.swipe(540, 1200, 540, 2100, 0.3, source="u2")  # 回顶
    await adapter.device.swipe(540, 1200, 540, 2100, 0.3)  # 顶部再拉一次幂等
    await adapter.goto("")

    # ── long_press:MIUI 桌面空白处长按 → 编辑模式(行为判据)──
    await adapter.press_key("home")
    await asyncio.sleep(1.2)
    for source in ("u2", "scrcpy"):  # 两条通道分别验证长按语义
        used = await adapter.device.long_press(540, 1500, hold_s=0.8, source=source)
        assert used == source
        await asyncio.sleep(1.2)
        xml = await _dump(adapter)
        assert MENU_MARK in xml, f"source={source} 长按未进入桌面编辑模式"
        await adapter.back()
        await asyncio.sleep(0.8)


@pytest.mark.device
async def test_contract_scroll_presskey_use_channel(adapter, mobile_config):
    """契约 scroll/home 的注入透明走 scrcpy 通道(injection_source=auto 默认)。

    行为判据与契约 scroll 用例同源(列表到底);通道使用证据:
    scroll 后 session.healthy_channel() 非空(auto 首用即启动通道)。
    """
    await adapter.goto("")
    assert await adapter.wait_for(Target(Locator.TEXT, "WLAN"))
    await adapter.scroll("down", 1200)
    assert await adapter.wait_for(Target(Locator.TEXT, "桌面")), "滑动未到列表底部"
    assert (
        adapter._session.healthy_channel() is not None
    ), "auto 注入应已启动 scrcpy 通道"

    await adapter.press_key("home")  # back/home 走同一通道
    await asyncio.sleep(1.0)
    await adapter.goto("")
    assert await adapter.wait_for(Target(Locator.TEXT, "WLAN"))

    t0 = time.monotonic()
    for _ in range(2):
        await adapter.scroll("down", 900)
        await adapter.scroll("up", 900)
    print(f"\n契约 4 连滑(透明 auto 注入)墙钟: {time.monotonic() - t0:.2f}s")


@pytest.mark.device
async def test_dense_sequence_scrcpy_vs_u2(adapter, mobile_config):
    """密集滑动 6 连发:同一序列分别经 u2 与 scrcpy 执行,打印墙钟。

    断言:两通道全部完成、scrcpy 总时长不慢于 u2(每 op 省一次 RPC
    往返,预期显著更快;不做固定时间断言,数字供 validation.md 引用)。
    """
    await adapter.goto("")
    assert await adapter.wait_for(Target(Locator.TEXT, "WLAN"))

    async def dense(source: str) -> float:
        t0 = time.monotonic()
        for i in range(3):
            used = await adapter.device.swipe(540, 1800, 540, 800, 0.25, source=source)
            assert used == source, f"第 {i} 次 down 未按指定通道执行:{used}"
            used = await adapter.device.swipe(540, 900, 540, 2000, 0.25, source=source)
            assert used == source
        return time.monotonic() - t0

    u2_total = await dense("u2")
    scrcpy_total = await dense("scrcpy")
    print(f"\n密集 6 连滑墙钟:u2={u2_total:.2f}s scrcpy={scrcpy_total:.2f}s "
          f"(省 {(u2_total - scrcpy_total) * 1000:.0f}ms)")
    assert scrcpy_total <= u2_total + 0.1, (
        f"scrcpy({scrcpy_total:.2f}s) 应不慢于 u2({u2_total:.2f}s)"
    )
    # 收尾回到列表顶部,不影响后续用例
    await adapter.device.swipe(540, 900, 540, 2000, 0.3, source="u2")
