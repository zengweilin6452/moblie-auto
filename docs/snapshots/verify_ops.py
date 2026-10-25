"""Beta_Academy 1.9.77 真机自动化操作全量验证(用项目自身 adapter 代码)。"""
import asyncio, io, sys, time
from pathlib import Path

ROOT = Path(r"D:\workspace\claudecode-test\moblie-auto")
sys.path.insert(0, str(ROOT / "src"))

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config
from mobile_auto.core.backend import enumerate_device_states
from mobile_auto.errors import ElementNotFound
from mobile_auto.config import ConfigurationError

SNAP = ROOT / "docs" / "snapshots"
SERIAL = "749622c9"
results = []


def record(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:200]))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" | " + str(detail)[:200]) if detail else ""), flush=True)


async def bcall(session, fn, timeout=25):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: session.submit(fn).result(timeout))


def save_shot(data, name):
    p = SNAP / name
    p.write_bytes(data)
    return f"{name}({len(data)//1024}KB)"


async def main():
    # ---- 1. 设备枚举 ----
    try:
        states = enumerate_device_states()
        record("enumerate_device_states", states.get(SERIAL) == "device", states)
    except Exception as e:
        record("enumerate_device_states", False, repr(e))
        raise

    config = load_config(
        device_serial=SERIAL,
        app_package="com.sinosoft.trainapp",
        app_activity=".MainActivity",
        launch_ready_target=Target(Locator.TEXT, "首页"),
        assets_dir=str(SNAP / "assets"),
        artifacts_dir=str(SNAP / "artifacts"),
    )
    session = DeviceSession(config)
    t0 = time.monotonic()
    session.start()
    record("session.start (u2 connect)", True, f"{time.monotonic()-t0:.1f}s")

    adapter = MobileAdapter(session)
    home_marker = Target(Locator.TEXT, "内训班级管理")  # 首页独有区块

    try:
        # ---- 2. 只读:screen_size / dump_ui / screenshot ----
        size = await bcall(session, lambda b: b.screen_size())
        record("backend.screen_size", size == (1080, 2400), size)

        xml = await bcall(session, lambda b: b.dump_ui())
        record("backend.dump_ui", "首页" in xml, f"len={len(xml)}, 含'首页'={'首页' in xml}")

        png = await adapter.screenshot()
        record("adapter.screenshot", png.startswith(b"\x89PNG") and len(png) > 50_000,
               save_shot(png, "ops_01_home.png"))

        # ---- 3. 定位方式 ----
        els = await adapter.find_elements(Target(Locator.TEXT, "首页"))
        record("find_elements TEXT 首页", len(els) >= 1, f"count={len(els)}")

        els = await adapter.find_elements(Target(Locator.ACCESSIBILITY_ID, "Search"))
        record("find_elements ACCESSIBILITY_ID Search", len(els) >= 1, f"count={len(els)}")

        els = await adapter.find_elements(Target(Locator.XPATH, '//android.widget.TextView[@text="课程"]'))
        record("find_elements XPATH 课程", len(els) >= 1, f"count={len(els)}")

        els = await adapter.find_elements(Target(Locator.U2_SELECTOR, '{"text":"培训班级"}'))
        record("find_elements U2_SELECTOR textContains", len(els) >= 1, f"count={len(els)}")

        els = await adapter.find_elements(Target(Locator.RESOURCE_ID, "com.sinosoft.trainapp:id/none"))
        record("find_elements RESOURCE_ID (无匹配不报错)", els == [], f"count={len(els)}")

        # ---- 4. 可见性/等待 ----
        vis = await adapter.is_visible(Target(Locator.TEXT, "首页"))
        record("is_visible TEXT 首页", vis is True, vis)

        t0 = time.monotonic()
        w = await adapter.wait_for(Target(Locator.TEXT, "课程"), timeout=5)
        record("wait_for TEXT 课程 (命中)", w is True, f"{time.monotonic()-t0:.1f}s")

        t0 = time.monotonic()
        w = await adapter.wait_for(Target(Locator.TEXT, "绝不存在的元素XYZ"), timeout=2)
        record("wait_for 不存在目标超时返回 False", w is False, f"{time.monotonic()-t0:.1f}s")

        # ---- 5. 属性读取 ----
        txt = await adapter.get_text(home_marker)
        record("get_text 内训班级管理", txt == "内训班级管理", repr(txt))

        b = await adapter.get_attribute(home_marker, "bounds")
        ok = isinstance(b, tuple) and len(b) == 4 and b[2] > b[0] and b[3] > b[1]
        record("get_attribute bounds", ok, b)
        t = await adapter.get_attribute(home_marker, "text")
        record("get_attribute text", t == "内训班级管理", repr(t))

        # ---- 6. ElementHandle 与 click:切课程 tab 再切回 ----
        els = await adapter.find_elements(Target(Locator.TEXT, "课程"))
        record("ElementHandle.get_text", bool(els) and els[0].get_text() == "课程",
               els[0].get_text() if els else "no element")
        await els[0].click()
        await asyncio.sleep(1.5)
        gone = not await adapter.is_visible(home_marker)
        record("ElementHandle.click 课程tab (首页区块消失)", gone)
        png = await adapter.screenshot()
        save_shot(png, "ops_02_course_tab.png")

        await adapter.click(Target(Locator.TEXT, "首页"))
        await asyncio.sleep(1.5)
        back_home = await adapter.is_visible(home_marker)
        record("adapter.click 首页tab (区块恢复)", back_home)

        # ---- 7. 子页导航 + back ----
        await adapter.click(Target(Locator.TEXT, "直播中心"))
        await asyncio.sleep(2.0)
        png = await adapter.screenshot()
        save_shot(png, "ops_03_live_center.png")
        xml = await bcall(session, lambda b: b.dump_ui())
        in_live = "内训班级管理" not in xml
        record("click 直播中心 进入子页", in_live, "首页区块已不在" if in_live else "仍在首页?")
        await adapter.back()
        await asyncio.sleep(1.5)
        back_ok = await adapter.is_visible(home_marker)
        record("back() 返回首页", back_ok)

        # ---- 8. scroll ----
        await adapter.scroll("down", 900)
        await asyncio.sleep(1.0)
        xml_down = await bcall(session, lambda b: b.dump_ui())
        scrolled = "内训班级管理" not in xml_down
        record("scroll down 900px (内容滚动)", scrolled,
               "区块滚出视口" if scrolled else "区块仍可见")
        await adapter.scroll("up", 900)
        await asyncio.sleep(1.0)
        up_ok = await adapter.is_visible(home_marker)
        record("scroll up 900px (回滚)", up_ok)

        # ---- 9. 图像模板:注册 + 匹配 + 点击 ----
        from mobile_auto.imaging.store import TemplateStore
        png = await adapter.screenshot()
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        tpl = img.crop((925, 873, 974, 922))  # 首页 Search 放大镜图标
        buf = io.BytesIO()
        tpl.save(buf, format="PNG")
        store = TemplateStore(SNAP / "assets")
        try:
            store.create("verify/search_icon.png", buf.getvalue(),
                         source_width=img.width, source_height=img.height,
                         app_package="com.sinosoft.trainapp", note="真机验证模板")
            record("TemplateStore.create 注册模板", True, f"{tpl.size} from {img.size}")
        except Exception as e:
            record("TemplateStore.create 注册模板", False, repr(e))

        img_target = Target(Locator.IMAGE, "verify/search_icon.png")
        vis = await adapter.is_visible(img_target)
        record("is_visible IMAGE Search图标", vis is True, vis)
        els = await adapter.find_elements(img_target)
        record("find_elements IMAGE", len(els) == 1, f"count={len(els)}")

        await adapter.click(img_target)
        await asyncio.sleep(2.0)
        png = await adapter.screenshot()
        save_shot(png, "ops_04_search_page.png")
        xml = await bcall(session, lambda b: b.dump_ui())
        has_edit = "EditText" in xml
        record("IMAGE click 进入搜索页", "内训班级管理" not in xml, f"EditText={'有' if has_edit else '无'}")

        # ---- 10. input_text ----
        if has_edit:
            edit_target = Target(Locator.U2_SELECTOR, '{"className":"android.widget.EditText"}')
            await adapter.input_text(edit_target, "培训")
            await asyncio.sleep(0.8)
            xml = await bcall(session, lambda b: b.dump_ui())
            typed = "培训" in xml
            record("input_text 输入'培训'", typed)
            png = await adapter.screenshot()
            save_shot(png, "ops_05_typed.png")
            await adapter.back()
            await asyncio.sleep(1.0)
            if not await adapter.is_visible(home_marker):
                await adapter.back()
                await asyncio.sleep(1.0)
            back_ok = await adapter.is_visible(home_marker)
            record("back 返回首页(搜索页退出)", back_ok)

        # ---- 11. 负向:不存在的元素点击应报 ElementNotFound ----
        t0 = time.monotonic()
        try:
            await adapter.click(Target(Locator.TEXT, "绝不存在的元素XYZ"))
            record("click 不存在目标报 ElementNotFound", False, "未抛错")
        except ElementNotFound:
            record("click 不存在目标报 ElementNotFound", True, f"{time.monotonic()-t0:.1f}s 后抛出")

        # ---- 12. deeplink 白名单拒绝 ----
        try:
            await adapter.goto("foo://bar")
            record("goto 非白名单 deeplink 拒绝", False, "未抛错")
        except ConfigurationError:
            record("goto 非白名单 deeplink 拒绝", True)

        # ---- 13. press_key home + 冷启动 + wait_for_load ----
        await adapter.press_key("home")
        await asyncio.sleep(1.5)
        t0 = time.monotonic()
        await adapter.goto("")
        await adapter.wait_for_load()
        record("press_key home + goto('') 冷启动 + wait_for_load", True,
               f"冷启动到首页就绪 {time.monotonic()-t0:.1f}s")
        png = await adapter.screenshot()
        save_shot(png, "ops_06_after_coldstart.png")

    finally:
        session.close()
        record("session.close", True)

    print("\n===== 汇总 =====")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"通过 {passed}/{len(results)}")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name} | {detail}")


if __name__ == "__main__":
    asyncio.run(main())
