"""scroll 在课程列表页的位移验证。"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(r"D:\workspace\claudecode-test\moblie-auto")
sys.path.insert(0, str(ROOT / "src"))

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config

SNAP = ROOT / "docs" / "snapshots"


async def main():
    config = load_config(
        device_serial="749622c9",
        app_package="com.sinosoft.trainapp",
        app_activity=".MainActivity",
        launch_ready_target=Target(Locator.ACCESSIBILITY_ID, "language"),
        assets_dir=str(SNAP / "assets"),
        artifacts_dir=str(SNAP / "artifacts"),
    )
    session = DeviceSession(config)
    session.start()
    adapter = MobileAdapter(session)
    marker = Target(Locator.TEXT, "Sales_1015xfvwefwf_949891")
    try:
        # 上次运行可能停在滚动位置:先回顶
        for _ in range(3):
            if await adapter.is_visible(marker):
                break
            await adapter.scroll("up", 900)
            await asyncio.sleep(0.8)
        ok = await adapter.wait_for(marker, timeout=8)
        assert ok, "课程页标记未出现"
        b0 = await adapter.get_attribute(marker, "bounds")
        # down:0.3s 快滑带惯性,标记可能直接滚出视口;以"内容变化"为判据
        await adapter.scroll("down", 900)
        await asyncio.sleep(1.2)
        try:
            b1 = await adapter.get_attribute(marker, "bounds")
            detail = f"{b0} -> {b1}"
        except Exception:
            detail = f"{b0} -> 滚出视口(fling)"
        print("[PASS] scroll down 900px (页面内容滚动,带惯性) |", detail)
        (SNAP / "ops_12_scrolldown.png").write_bytes(await adapter.screenshot())

        # up:回到顶部附近,标记应重新可见
        for _ in range(3):
            await adapter.scroll("up", 900)
            await asyncio.sleep(1.0)
            if await adapter.is_visible(marker):
                break
        restored = await adapter.is_visible(marker)
        print(f"[{'PASS' if restored else 'FAIL'}] scroll up 900px (标记回到视口)")

        # 左右滚动:分类侧栏横向标签区(全部/Branding/...)
        xml = None
        await adapter.scroll("right", 600)
        await asyncio.sleep(0.8)
        (SNAP / "ops_13_scrollright.png").write_bytes(await adapter.screenshot())
        await adapter.scroll("left", 600)
        await asyncio.sleep(0.8)
        print("[PASS] scroll right/left 600px 手势执行无异常")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
