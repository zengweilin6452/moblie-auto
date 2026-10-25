"""深场景可行性探查:课程列表结构(滚动容器/嵌套)、我的页 WebView 入口。

只探查不动业务状态(仅 tab 切换);dump 全部存档供分析。
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


def scrollable_nodes(xml: str) -> list[dict]:
    """提取 scrollable=true 节点:bounds、class、resource-id、文本线索。"""
    out = []
    for m in re.finditer(r'<node[^>]*scrollable="true"[^>]*>', xml):
        node = m.group(0)
        out.append(
            {
                "class": (re.search(r'class="([^"]*)"', node) or [None, "?"])[1],
                "resource-id": (re.search(r'resource-id="([^"]*)"', node) or [None, ""])[1],
                "bounds": (re.search(r'bounds="([^"]*)"', node) or [None, "?"])[1],
                "desc": (re.search(r'content-desc="([^"]*)"', node) or [None, ""])[1],
            }
        )
    return out


def webview_nodes(xml: str) -> list[str]:
    return sorted(set(re.findall(r'class="([^"]*WebView[^"]*)"', xml)))


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
        home = await dump_ui(session)
        (SNAP / "deep_home.xml").write_text(home, encoding="utf-8")
        print("== 首页 scrollable:", scrollable_nodes(home))
        print("== 首页 webview:", webview_nodes(home))

        # 课程 tab(text 优先,语言漂移时试英文)
        tab = Target(Locator.TEXT, "课程")
        if not await adapter.is_visible(tab):
            print("!! 课程 tab 不可见,跳过(记录语言现状)")
        else:
            await adapter.click(tab)
            await asyncio.sleep(1.5)
            course = await dump_ui(session)
            (SNAP / "deep_course.xml").write_text(course, encoding="utf-8")
            print("== 课程页 scrollable:", scrollable_nodes(course))
            icons = len(re.findall(r'content-desc="courseIcon"', course))
            print("== 课程页 courseIcon 数:", icons)
            print("== 课程页 webview:", webview_nodes(course))

        # 我的 tab:找协议/设置类入口
        mine_tab = Target(Locator.TEXT, "我的")
        if await adapter.is_visible(mine_tab):
            await adapter.click(mine_tab)
            await asyncio.sleep(1.5)
            mine = await dump_ui(session)
            (SNAP / "deep_mine.xml").write_text(mine, encoding="utf-8")
            texts = [t for t in re.findall(r'text="([^"]{1,24})"', mine) if t]
            print("== 我的页 texts:", texts[:40])
            print("== 我的页 scrollable:", scrollable_nodes(mine))
            print("== 我的页 webview:", webview_nodes(mine))
        await adapter.press_key("home")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
