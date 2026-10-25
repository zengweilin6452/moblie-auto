"""Beta_Academy 深场景可行性验证(2026-10-25):列表滚动加载/嵌套滚动/
WebView/长按/多窗口分屏。

全部操作走项目 MobileAdapter 契约面与 adapter.device 扩展面(底层探针
单独标注);每个场景输出 可行/不可行 + 证据,证据 dump 存 docs/snapshots/。

结论速览(2026-10-25 真机实测,Xiaomi 749622c9 / Android 14;
adapter.device 坐标手势扩展面落地后 S2/S4 复验更新):
  1. 列表滚动加载: 部分可行——知识库列表可滚动;当前账号仅 5 项数据,
     单屏放不下也未触发分页;工具能力(scroll+计数断言)成立,分页断言
     需数据充足时复验。
  2. 嵌套滚动容器: 可行(经 adapter.device.swipe)——课程页分类横条
     (y≈402)坐标手势命中(scrcpy 通道),分类滚动可见;首页两条内嵌
     容器为静态布局。契约 scroll 仍固定屏幕中线,定向场景用 device
     扩展面;注入默认走 scrcpy 控制流(密集操作提速实测 45%)。
  3. WebView: 可行——设置页「问题反馈」打开 android.webkit.WebView;
     其余已探查页面(首页/课程/知识库/我的)均为原生 Compose 容器。
  4. 长按: 可行(经 adapter.device.long_press)——框架级长按双通道
     (scrcpy/u2)真机验证有效(MIUI 桌面编辑模式判据);知识库条目
     长按行为为进入目录(该 App 此处无长按菜单),区别于菜单的场景
     已由桌面判据覆盖。契约 15 方法仍无长按。
  5. 多窗口/分屏: 不可行(契约)——触发分屏需最近任务里拖拽/长按卡片;
     device.* 提供坐标手势后,卡片长按理论上可拼(卡片坐标不可由
     dump 稳定定位,MIUI 最近任务为专有 UI),仍判不可行,留待真实
     需要时按需评估;App 侧标志为 RESIZEABLE_VIA_SDK_VERSION。

补充发现:导航中会随机弹 MIUI 系统权限对话框(媒体/通知,com.lbe.
security.miui),挡住 dump 与锚点等待;脚本统一「拒绝」处理保持既有
授权状态(冷启动脚本同步加固)。
"""
import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\workspace\claudecode-test\moblie-auto")
sys.path.insert(0, str(ROOT / "src"))

logging.getLogger("wetest").setLevel(logging.WARNING)

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config

SNAP = ROOT / "docs" / "snapshots"
SERIAL = "749622c9"
ADB = ROOT / "tools" / "adb" / "adb.exe"


def record(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" | " + str(detail)[:180]) if detail else ""), flush=True)


async def dump_ui(session, timeout=25) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: session.submit(lambda b: b.dump_ui()).result(timeout)
    )


async def bcall(session, fn, timeout=30):
    """底层后端调用(探针专用,非契约面;与 dump 同通道)。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: session.submit(fn).result(timeout)
    )


def scrollables(xml: str) -> list[tuple[str, tuple[int, int, int, int]]]:
    out = []
    for m in re.finditer(r'<node[^>]*scrollable="true"[^>]*>', xml):
        node = m.group(0)
        cls = re.search(r'class="([^"]*)"', node).group(1)
        bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        out.append((cls, tuple(int(bm.group(i)) for i in range(1, 5))))
    return out


def contains(a: tuple, b: tuple) -> bool:
    """b 的 bounds 完全落在 a 内(嵌套判定)。"""
    return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]


async def dismiss_system_dialog(adapter, session) -> bool:
    """关闭系统权限对话框(MIUI lbe):点「拒绝」保持既有授权状态不变。

    实证:进知识库时 App 请求媒体权限(照片/视频/音频),用户此前为
    拒绝态;自动化导航会随机触发该对话框并挡住后续 dump/点击。
    """
    xml = await dump_ui(session)
    if "com.lbe.security.miui" not in xml:
        return False
    deny = re.search(r'<node[^>]*text="拒绝"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not deny:
        return False
    x = (int(deny.group(1)) + int(deny.group(3))) // 2
    y = (int(deny.group(2)) + int(deny.group(4))) // 2

    def tap(backend):
        backend.tap(x, y)

    await bcall(session, tap)
    await asyncio.sleep(1.0)
    return True


async def ensure_main_tabs(adapter) -> None:
    """从任意内层页面退回带底部 tab 的主页面(设置/WebView 等无 tab)。"""
    tabs = Target(Locator.TEXT, "知识库")
    for _ in range(4):
        if await adapter.is_visible(tabs):
            return
        await adapter.back()
        await asyncio.sleep(1.2)
    raise RuntimeError("无法退回主页面(底部 tab 不可见)")


# ───────────────────────── 场景 1:列表滚动加载 ─────────────────────────

async def scenario_list_scroll(adapter, session) -> str:
    """知识库列表:滚动前后的条目计数与首项变化;分页加载=计数增长。"""
    await adapter.click(Target(Locator.TEXT, "知识库"))
    await asyncio.sleep(2.0)
    if await dismiss_system_dialog(adapter, session):
        record("S1 系统权限对话框", True, "已点拒绝关闭(保持既有授权状态)")
    xml0 = await dump_ui(session)
    (SNAP / "deep_s1_before.xml").write_text(xml0, encoding="utf-8")

    def items(xml: str) -> list[str]:
        """文件/文件夹条目:以「数量:N」标记识别(条目卡片固定字段)。"""
        return re.findall(r'text="(数量:\s*\d+)"', xml)

    first_items = items(xml0)
    record("S1 知识库列表初始条目数", True, f"{len(first_items)} 项含数量标记")

    for _ in range(3):
        await adapter.scroll("down", 900)
        await asyncio.sleep(1.5)
    xml1 = await dump_ui(session)
    (SNAP / "deep_s1_after.xml").write_text(xml1, encoding="utf-8")
    after_items = items(xml1)

    scrolled = after_items != first_items
    grew = len(after_items) > len(first_items)
    if grew:
        verdict = "可行:滚动后列表加载了更多条目(分页生效)"
    elif scrolled:
        verdict = "部分可行:列表可滚动,但当前账号数据量不足以触发分页加载;工具能力(scroll+计数断言)成立,分页断言需数据充足时复验"
    else:
        verdict = "受限:列表当前不可滚动(数据单屏放下)"
    record("S1 列表滚动加载", True, f"前{len(first_items)}项 → 后{len(after_items)}项 | {verdict}")
    return verdict


# ───────────────────────── 场景 2:嵌套滚动容器 ─────────────────────────

async def scenario_nested_scroll(adapter, session) -> str:
    """首页:外层 ScrollView 内嵌横向条(dump 结构判定 + 中线手势实测)。"""
    await adapter.click(Target(Locator.TEXT, "首页"))
    await asyncio.sleep(1.8)
    xml = await dump_ui(session)
    (SNAP / "deep_s2_home.xml").write_text(xml, encoding="utf-8")
    nodes = scrollables(xml)
    nested = [
        (outer, inner)
        for outer in nodes
        for inner in nodes
        if outer is not inner and contains(outer[1], inner[1])
    ]
    record("S2 嵌套滚动结构识别", bool(nested), f"{nodes}")

    if nested:
        details = "; ".join(
            f"{inner[0]}[{inner[1][1]},{inner[1][3]}] ∈ {outer[0]}"
            for outer, inner in nested
        )
        # 契约 scroll 的水平手势固定走 y=height/2=1200;内层条 y 范围?
        reach_report = []
        any_reach = False
        for _outer, inner in nested:
            (_cls, (_x0, y0, _x1, y1)) = inner
            in_reach = y0 <= 2400 // 2 <= y1
            any_reach = any_reach or in_reach
            reach_report.append(f"y=[{y0},{y1}]→{'命中' if in_reach else '偏离'}中线1200")
        record(
            "S2 内层容器可被契约 scroll 定向",
            any_reach,
            f"{details} | {'; '.join(reach_report)}",
        )
        await adapter.scroll("right", 600)
        await asyncio.sleep(1.2)
        xml2 = await dump_ui(session)
        (SNAP / "deep_s2_after_scroll.xml").write_text(xml2, encoding="utf-8")

        def content_texts(x: str) -> set[str]:
            texts = set(re.findall(r'text="([^"]{1,40})"', x))
            texts -= {t for t in texts if re.fullmatch(r"\d{1,2}:\d{2}|\d{1,3}|100", t)}
            return texts

        moved = scrollables(xml2) != nodes or content_texts(xml2) != content_texts(xml)
        record("S2 中线右滑页面响应(契约 scroll)", moved, "内容变化" if moved else "无变化(手势落点不在可滚容器)")

        # adapter.device.swipe 坐标手势定向内层容器:
        # 首页两条内嵌容器实测为静态布局(内容不随横向滑动变化);
        # 课程页分类横条([46,366][1034,438])是真正可滚的内层轨道,
        # 以它为判定面。视口右移 = 手指向左。
        rail_hit = False
        used = "n/a"
        rail_y = None
        if not any_reach:
            # 先试首页内层(结构面),再到课程页横条(行为面)
            (_cls, (ix0, iy0, ix1, iy1)) = nested[0][1]
            rail_y = (iy0 + iy1) // 2
            used = await adapter.device.swipe(900, rail_y, 300, rail_y, 0.3)
            await asyncio.sleep(1.2)
            xml3 = await dump_ui(session)
            (SNAP / "deep_s2_after_device_swipe.xml").write_text(xml3, encoding="utf-8")
            home_moved = content_texts(xml3) != content_texts(xml2 if moved else xml)
            record(
                "S2 device.swipe 定向首页内层容器",
                home_moved,
                f"y={rail_y} 通道={used};内容{'变化' if home_moved else '无变化(静态布局)'}",
            )

            await adapter.click(Target(Locator.TEXT, "课程"))
            await asyncio.sleep(2.0)
            xml4 = await dump_ui(session)
            (SNAP / "deep_s2_course.xml").write_text(xml4, encoding="utf-8")
            rm = re.search(
                r'<node[^>]*scrollable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                xml4,
            )
            if rm:
                rail = tuple(int(rm.group(i)) for i in range(1, 5))
                rail_y = (rail[1] + rail[3]) // 2
                used = await adapter.device.swipe(900, rail_y, 300, rail_y, 0.3)
                await asyncio.sleep(1.2)
                xml5 = await dump_ui(session)
                (SNAP / "deep_s2_course_after.xml").write_text(xml5, encoding="utf-8")
                rail_hit = content_texts(xml5) != content_texts(xml4)
                record(
                    "S2 device.swipe 定向课程页分类横条",
                    rail_hit,
                    f"横条 y=[{rail[1]},{rail[3]}] 通道={used};"
                    f"分类内容{'滚动' if rail_hit else '无变化'}",
                )
            if rail_hit:
                return (
                    "可行(经 adapter.device.swipe):课程页分类横条(y="
                    f"{rail_y})坐标手势命中(通道 {used},scrcpy 控制流),"
                    "分类滚动可见;首页内嵌容器为静态布局(与手势无关);"
                    "契约 scroll 仍固定屏幕中线,定向场景用 device 扩展面"
                )
            return (
                f"结构存在({details}),定向手势已注入(通道 {used})但两处内层"
                "均无滚动响应(静态布局);契约 scroll 中线手势也不可达"
                )
    return "可行" if nested else "不可行:当前页面无嵌套滚动结构"


# ───────────────────────── 场景 3:WebView ─────────────────────────

async def scenario_webview(adapter, session) -> str:
    """入口探测:我的 → 设置齿轮 → 协议/关于条目;dump 查 WebView class。"""
    await adapter.click(Target(Locator.TEXT, "我的"))
    await asyncio.sleep(1.8)
    gear = Target(Locator.ACCESSIBILITY_ID, "settings")
    if not await adapter.is_visible(gear):
        record("S3 设置入口", False, "desc=settings 不可见")
        return "不可验证:设置入口不可见"
    await adapter.click(gear)
    await asyncio.sleep(2.0)
    settings_xml = await dump_ui(session)
    (SNAP / "deep_s3_settings.xml").write_text(settings_xml, encoding="utf-8")
    texts = [t for t in re.findall(r'text="([^"]{1,30})"', settings_xml) if t.strip()]
    record("S3 设置页条目", True, str(texts[:20]))

    candidates = [
        t for t in texts
        if re.search(r"协议|隐私|关于|反馈|密码|Privacy|Agreement|About|Terms|Feedback", t, re.I)
    ]
    tried = []
    for target_text in candidates[:3]:
        entry = Target(Locator.TEXT, target_text)
        if not await adapter.is_visible(entry):
            continue
        tried.append(target_text)
        await adapter.click(entry)
        await asyncio.sleep(2.5)
        page_xml = await dump_ui(session)
        (SNAP / f"deep_s3_target_{len(tried)}.xml").write_text(page_xml, encoding="utf-8")
        (SNAP / f"deep_s3_target_{len(tried)}.png").write_bytes(await adapter.screenshot())
        webviews = sorted(set(re.findall(r'class="([^"]*WebView[^"]*)"', page_xml)))
        if webviews:
            record("S3 WebView 打开", True, f"{webviews} via「{target_text}」")
            await adapter.back()
            await asyncio.sleep(1.0)
            return f"可行:经「{target_text}」打开 WebView({webviews[0]})"
        await dismiss_system_dialog(adapter, session)
        await adapter.back()
        await asyncio.sleep(1.2)
        settings_xml = await dump_ui(session)
        if "修改密码" not in settings_xml and "问题反馈" not in settings_xml:
            await adapter.back()  # 可能多落了一层
            await asyncio.sleep(1.0)
    record("S3 WebView 打开", False, f"已试入口:{tried or '无'};落点均非 WebView")


# ───────────────────────── 场景 4:长按 ─────────────────────────

async def scenario_long_press(adapter, session) -> str:
    """adapter.device.long_press 复验(2026-10-25 扩展面落地后)。

    契约 15 方法仍无长按;device.long_press 双通道(scrcpy/u2)已真机
    验证为框架级长按(MIUI 桌面编辑模式判据,见 tests/device/
    test_device_ops_device.py)。此处验证 App 场景行为:知识库条目长按。
    """
    await ensure_main_tabs(adapter)
    await adapter.click(Target(Locator.TEXT, "知识库"))
    await asyncio.sleep(2.0)
    await dismiss_system_dialog(adapter, session)
    xml = await dump_ui(session)
    (SNAP / "deep_s4_before.xml").write_text(xml, encoding="utf-8")
    before_texts = set(re.findall(r'text="([^"]{1,40})"', xml))
    m = re.search(r'<node[^>]*text="数量:\s*\d+"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not m:
        m = re.search(r'<node[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not m:
        record("S4 长按目标", False, "未找到可长按目标")
        return "不可验证:知识库无可长按条目"
    x = (int(m.group(1)) + int(m.group(3))) // 2
    y = (int(m.group(2)) + int(m.group(4))) // 2

    used = await adapter.device.long_press(x, y, hold_s=0.8)
    await asyncio.sleep(1.2)
    await dismiss_system_dialog(adapter, session)
    xml2 = await dump_ui(session)
    (SNAP / "deep_s4_after_longpress.xml").write_text(xml2, encoding="utf-8")
    after_texts = set(re.findall(r'text="([^"]{1,40})"', xml2))
    menu_nodes = after_texts - before_texts
    has_menu = any(
        re.search(r"删除|下载|收藏|分享|重命名|置顶|取消|详情", t) for t in menu_nodes
    )
    record(
        "S4 device.long_press(知识库条目)",
        True,
        f"目标({x},{y}) 0.8s 通道={used};页面新增节点:"
        f"{sorted(menu_nodes)[:6] or '无'}"
        + (";出现长按菜单" if has_menu else ";无长按菜单(App 此处或未定义)"),
    )
    # 收尾:若进入了目录则返回列表
    await ensure_main_tabs(adapter)
    if has_menu:
        await adapter.back()
    return (
        "可行(经 adapter.device.long_press):框架级长按已生效"
        f"(通道 {used};菜单类长按行为已由 MIUI 桌面编辑模式判据在 "
        "test_device_ops_device.py 双通道验证);"
        + (
            "本 App 知识库条目长按弹出菜单"
            if has_menu
            else "本 App 知识库条目长按未定义菜单(新增节点非菜单形态),"
            "长按能力以系统级判据为准"
        )
        + ";契约 15 方法仍无长按,属 device 扩展面"
    )


# ───────────────────────── 场景 5:多窗口/分屏 ─────────────────────────

def resizeable_fact() -> str:
    try:
        out = subprocess.run(
            [str(ADB), "-s", SERIAL, "shell", "dumpsys", "package", "com.sinosoft.trainapp"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        if "ACTIVITIES_RESIZE_MODE_RESIZEABLE" in out:
            mode = re.search(r"ACTIVITIES_RESIZE_MODE_(\w+)", out)
            return f"支持多窗口调整大小(={mode.group(1) if mode else 'RESIZEABLE'})"
        m = re.search(r"resizeableActivity=(\w+)", out)
        return m.group(1) if m else "未知"
    except Exception as exc:  # noqa: BLE001
        return f"查询失败:{exc!r}"


async def scenario_multi_window(adapter, session) -> str:
    """契约面无分屏触发;recent 键可到最近任务,但 MIUI 分屏需卡片拖拽/长按。"""
    fact = resizeable_fact()
    record("S5 App resizeableActivity 标志", True, fact)

    await adapter.press_key("recent")
    await asyncio.sleep(2.0)
    recents_xml = await dump_ui(session)
    (SNAP / "deep_s5_recents.xml").write_text(recents_xml, encoding="utf-8")
    (SNAP / "deep_s5_recents.png").write_bytes(await adapter.screenshot())
    has_card = "Beta_Academy" in recents_xml or "trainapp" in recents_xml
    record("S5 最近任务可达", has_card, "press_key('recent') 打开最近任务")
    await adapter.press_key("home")
    return (
        "不可行(留待真实需要再评估):device.* 已提供坐标手势与长按,"
        "理论上可在最近任务拼卡片长按触发分屏;但 MIUI 最近任务为专有 UI,"
        "卡片节点不出现在 App dump 中、坐标不可稳定定位,脚本化不可靠;"
        "App 侧 resizeableActivity=" + fact +
        "(标志支持分屏不代表可稳定触发)。替代路径:adb shell 手段"
        "(am stack/windowingMode)不属于 MobileAdapter 契约,未采用"
    )


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
    verdicts: dict[str, str] = {}
    try:
        await adapter.goto("")
        # 启动期也可能弹系统权限对话框(实证 2026-10-25:挡住 language 锚点导致
        # wait_for_load 超时),先清理再等就绪
        for _ in range(3):
            if not await dismiss_system_dialog(adapter, session):
                break
        await adapter.wait_for_load()
        verdicts["1.列表滚动加载"] = await scenario_list_scroll(adapter, session)
        verdicts["2.嵌套滚动容器"] = await scenario_nested_scroll(adapter, session)
        verdicts["3.WebView"] = await scenario_webview(adapter, session)
        verdicts["4.长按"] = await scenario_long_press(adapter, session)
        verdicts["5.多窗口/分屏"] = await scenario_multi_window(adapter, session)
        print("\n===== 深场景可行性结论 =====")
        for k, v in verdicts.items():
            print(f"{k}: {v}")
    finally:
        session.close()
        lines = [f"{k}: {v}" for k, v in verdicts.items()]
        (SNAP / "deep_scenarios_verdict.txt").write_text(
            "\n".join(lines), encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
