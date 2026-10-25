"""修正判定后的补验:ElementHandle.get_text、tab 切换时序、back、scroll 位移、IMAGE 点击。"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(r"D:\workspace\claudecode-test\moblie-auto")
sys.path.insert(0, str(ROOT / "src"))

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config

SNAP = ROOT / "docs" / "snapshots"
results = []


def record(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:200]))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" | " + str(detail)[:200]) if detail else ""), flush=True)


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
    home_marker = Target(Locator.TEXT, "内训班级管理")
    create_btn = Target(Locator.TEXT, "创建班级 +")

    async def bcall(fn, timeout=25):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: session.submit(fn).result(timeout))

    async def wait_absent(target, timeout=8):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if not await adapter.is_visible(target):
                return time.monotonic() - t0
            await asyncio.sleep(0.4)
        return None

    async def wait_present(target, timeout=8):
        ok = await adapter.wait_for(target, timeout=timeout)
        return ok

    try:
        assert await wait_present(home_marker, 8), "起始不在首页"
        print("起始状态:首页 OK", flush=True)

        # ---- 1. ElementHandle.get_text / get_attribute ----
        els = await adapter.find_elements(Target(Locator.TEXT, "课程"))
        txt = await els[0].get_text()
        record("ElementHandle.get_text", txt == "课程", repr(txt))
        b = await els[0].get_attribute("bounds")
        record("ElementHandle.get_attribute bounds",
               isinstance(b, tuple) and len(b) == 4 and b[3] > b[1], b)

        # ---- 2. ElementHandle.click 课程tab → 轮询首页区块消失 ----
        t0 = time.monotonic()
        await els[0].click()
        dt = await wait_absent(home_marker)
        record("ElementHandle.click 课程tab (首页区块消失)", dt is not None,
               f"{dt:.1f}s" if dt else "8s 未消失")
        (SNAP / "ops_08_course_tab2.png").write_bytes(await adapter.screenshot())

        # ---- 3. adapter.click 首页tab → 轮询区块恢复 ----
        t0 = time.monotonic()
        await adapter.click(Target(Locator.TEXT, "首页"))
        ok = await wait_present(home_marker, 10)
        record("adapter.click 首页tab (区块恢复)", ok, f"{time.monotonic()-t0:.1f}s")

        # ---- 4. 直播中心 子页 + back ----
        await adapter.click(Target(Locator.TEXT, "直播中心"))
        dt = await wait_absent(home_marker, 10)
        record("click 直播中心 进入子页", dt is not None, f"{dt:.1f}s" if dt else "10s 未离开首页")
        (SNAP / "ops_09_live2.png").write_bytes(await adapter.screenshot())
        await adapter.back()
        ok = await wait_present(home_marker, 10)
        record("back() 返回首页", ok)

        # ---- 5. scroll:区块位移实测 ----
        b0 = await adapter.get_attribute(create_btn, "bounds")
        await adapter.scroll("down", 900)
        await asyncio.sleep(1.0)
        b1 = await adapter.get_attribute(create_btn, "bounds")
        moved = b1 is not None and b0 is not None and (b0[1] - b1[1]) >= 400
        record("scroll down 900px (创建班级+上移)", moved, f"{b0} -> {b1}")
        await adapter.scroll("up", 900)
        await asyncio.sleep(1.0)
        b2 = await adapter.get_attribute(create_btn, "bounds")
        restored = b2 is not None and abs(b2[1] - b0[1]) <= 150
        record("scroll up 900px (位置恢复)", restored, f"{b0} -> {b2}")

        # ---- 6. IMAGE click:成功判据=搜索页 EditText 出现 ----
        img_target = Target(Locator.IMAGE, "verify/search_icon.png")
        t0 = time.monotonic()
        await adapter.click(img_target)
        edit = Target(Locator.U2_SELECTOR, '{"className":"android.widget.EditText"}')
        ok = await wait_present(edit, 8)
        record("IMAGE click Search图标 (搜索页 EditText 出现)", ok, f"{time.monotonic()-t0:.1f}s")
        (SNAP / "ops_10_search2.png").write_bytes(await adapter.screenshot())

        # ---- 7. ElementHandle.input_text ----
        els = await adapter.find_elements(edit)
        if els:
            await els[0].input_text("BYD培训")
            await asyncio.sleep(0.8)
            xml = await bcall(lambda b: b.dump_ui())
            record("ElementHandle.input_text 'BYD培训'", "BYD培训" in xml)
            (SNAP / "ops_11_typed2.png").write_bytes(await adapter.screenshot())
        else:
            record("ElementHandle.input_text 'BYD培训'", False, "未找到 EditText")

        # ---- 8. 退出搜索页回首页 ----
        await adapter.back()
        await asyncio.sleep(1.0)
        if not await adapter.is_visible(home_marker):
            await adapter.back()
            await asyncio.sleep(1.0)
        record("back 退出搜索页回首页", await adapter.is_visible(home_marker))

    finally:
        session.close()

    print("\n===== 补验汇总 =====")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"通过 {passed}/{len(results)}")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name} | {detail}")


if __name__ == "__main__":
    asyncio.run(main())
