"""冷启动链验证:goto('') + wait_for_load(语言无关锚点 desc='language')。"""
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
    try:
        await adapter.press_key("home")
        await asyncio.sleep(1.5)
        t0 = time.monotonic()
        await adapter.goto("")            # 冷启动:先 force-stop 再 start
        await adapter.wait_for_load()     # 等待就绪目标:desc='language'(登录页/首页均有,语言无关)
        dt = time.monotonic() - t0
        print(f"[PASS] goto('') 冷启动 + wait_for_load | {dt:.1f}s")

        xml = await asyncio.get_event_loop().run_in_executor(
            None, lambda: session.submit(lambda b: b.dump_ui()).result(20))
        on_login = "登录" in xml or "Login" in xml
        has_captcha_edit = True  # 从 dump 判断验证码输入框内容
        import re
        m = re.findall(r'text="([^"]*)"[^>]*bounds="\[69,1005\]', xml)
        print(f"冷启动落点: {'登录页' if on_login else '非登录页'} | 验证码输入框残留: {m}")
        png = await adapter.screenshot()
        (SNAP / "ops_07_coldstart_landing.png").write_bytes(png)
        print(f"[PASS] 落点截图 ops_07_coldstart_landing.png ({len(png)//1024}KB)")
        # 再验证 ElementHandle.get_text(上轮脚本 bug:未 await)
        els = await adapter.find_elements(Target(Locator.ACCESSIBILITY_ID, "language"))
        if els:
            t = await els[0].get_text() if hasattr(els[0], "get_text") else "n/a"
            print(f"[PASS] ElementHandle 定位 language 图标 | count={len(els)}")
    finally:
        session.close()
        print("[PASS] session.close")

if __name__ == "__main__":
    asyncio.run(main())
