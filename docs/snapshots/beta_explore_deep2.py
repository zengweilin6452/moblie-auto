"""深场景探查第二轮:知识库列表(滚动加载候选)+ WebView 入口探测。

导航仅"打开→查看→返回",不做业务提交。
"""
import asyncio
import logging
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\workspace\claudecode-test\moblie-auto")
sys.path.insert(0, str(ROOT / "src"))

logging.getLogger("wetest").setLevel(logging.WARNING)

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config

SNAP = ROOT / "docs" / "snapshots"
SERIAL = "749622c9"


async def dump_ui(session, timeout=25) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: session.submit(lambda b: b.dump_ui()).result(timeout)
    )


def summary(tag: str, xml: str) -> None:
    texts = [t for t in re.findall(r'text="([^"]{1,40})"', xml) if t.strip()]
    scrolls = re.findall(r'<node[^>]*scrollable="true"[^>]*bounds="(\[[^"]*\])"', xml)
    webviews = sorted(set(re.findall(r'class="([^"]*WebView[^"]*)"', xml)))
    print(f"== {tag}: scrollable={scrolls} webview={webviews}")
    print(f"   texts({len(texts)}): {texts[:28]}")


async def main():
    config = load_config(
        device_serial=SERIAL,
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
        await adapter.goto("")
        await adapter.wait_for_load()

        kb = Target(Locator.TEXT, "知识库")
        if not await adapter.is_visible(kb):
            print("!! 知识库 tab 不可见")
            return
        await adapter.click(kb)
        await asyncio.sleep(2.0)
        kb_xml = await dump_ui(session)
        (SNAP / "deep_kb.xml").write_text(kb_xml, encoding="utf-8")
        summary("知识库", kb_xml)

        # 知识库首项(非 tab 文本):点开看是否 WebView 阅读器
        entries = [
            t for t in re.findall(r'text="([^"]{2,40})"', kb_xml)
            if t.strip() and t not in ("知识库", "首页", "课程", "我的", "全部")
        ]
        print("   候选条目:", entries[:10])
        if entries:
            item = Target(Locator.TEXT, entries[0])
            await adapter.click(item)
            await asyncio.sleep(2.5)
            detail = await dump_ui(session)
            (SNAP / "deep_kb_detail.xml").write_text(detail, encoding="utf-8")
            summary("知识库详情", detail)
            (SNAP / "deep_kb_detail.png").write_bytes(await adapter.screenshot())
            await adapter.back()
            await asyncio.sleep(1.2)

        # 首页 OJEC检核 Go:可能的 H5 入口
        await adapter.click(Target(Locator.TEXT, "首页"))
        await asyncio.sleep(1.5)
        go = Target(Locator.TEXT, "Go")
        if await adapter.is_visible(go):
            await adapter.click(go)
            await asyncio.sleep(2.5)
            go_xml = await dump_ui(session)
            (SNAP / "deep_go.xml").write_text(go_xml, encoding="utf-8")
            summary("OJEC Go 落点", go_xml)
            (SNAP / "deep_go.png").write_bytes(await adapter.screenshot())
            await adapter.back()
            await asyncio.sleep(1.2)
        await adapter.press_key("home")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
