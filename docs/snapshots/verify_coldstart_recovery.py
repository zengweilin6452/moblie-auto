"""Beta_Academy 冷启动落点不定:复现 + 稳定化方案验证(2026-10-25)。

问题背景(2026-10-24 验证发现):force-stop 后重启,多数情况自动恢复
登录直达首页,偶发停在登录页(记住密码回填账号/密码/验证码,验证码为
过期残留;图形验证码 OCR 不可自动登录)。

2026-10-25 实证结论(10 次冷启动 + 20 次重启重试):
- 会话有效期内,冷启动落点 100% 稳定为首页(goto('')+wait_for_load
  (desc='language') 后 1.3s 内就绪,10/10)。
- 落点由「App 会话是否有效」决定,不是随机竞态:首页判据为
  desc=Search(语言无关,登录页没有);登录页为多个 EditText(账号/
  密码/验证码)。首页的搜索框也是 EditText,不能以 EditText 存在性分类。
- 推论:重启重试救不了会话过期的登录页落点(服务端状态,不在客户端);
  恢复分支保留一次廉价重试覆盖瞬态失败,仍登录页则明确报错要求人工
  登录一次(图形验证码不可自动化)。

用法:python verify_coldstart_recovery.py [迭代次数,默认 10]
"""
import asyncio
import logging
import re
import sys
import time
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


def classify(xml: str) -> tuple[str, dict]:
    """落点分类(2026-10-25 实证修正):

    - 首页:desc=Search(首页搜索框,语言无关;登录页没有)且 desc=language;
      首页有 1 个 EditText(搜索框"请输入您感兴趣文档资料"),不能拿
      EditText 存在性判登录页。
    - 登录页:多个 EditText(账号/密码/图形验证码)且无 desc=Search;
      文本标记(登录/Log)受语言漂移影响只作参考。
    """
    edit_boxes = len(re.findall(r'class="android\.widget\.EditText"', xml))
    markers = {
        "EditText": edit_boxes,
        "desc=language": 'content-desc="language"' in xml,
        "desc=Search": 'content-desc="Search"' in xml,
        "底部tab(首页/我的)": ("首页" in xml or "Home" in xml) and ("我的" in xml or "Mine" in xml),
        "文本含登录/Log": ("登录" in xml or "Log in" in xml or "Login" in xml),
    }
    if markers["desc=Search"] and markers["desc=language"]:
        return "home", markers
    if edit_boxes >= 2 and not markers["desc=Search"]:
        return "login", markers
    return "unknown", markers


async def launch_once(adapter, session) -> tuple[str, dict, float]:
    t0 = time.monotonic()
    await adapter.goto("")  # 冷启动:先 force-stop 再 start
    # 启动期可能弹 MIUI 系统权限对话框(媒体/通知),点「拒绝」保持既有授权
    # 状态;对话框挡住 language 锚点会让 wait_for_load 超时(2026-10-25 实证)
    for _ in range(3):
        xml = await dump_ui(session)
        if "com.lbe.security.miui" not in xml:
            break
        m = re.search(
            r'<node[^>]*text="拒绝"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
        )
        if not m:
            break
        loop = asyncio.get_running_loop()
        x = (int(m.group(1)) + int(m.group(3))) // 2
        y = (int(m.group(2)) + int(m.group(4))) // 2
        await loop.run_in_executor(
            None, lambda: session.submit(lambda b: b.tap(x, y)).result(15)
        )
        await asyncio.sleep(1.0)
    await adapter.wait_for_load()
    xml = await dump_ui(session)
    dt = time.monotonic() - t0
    landing, markers = classify(xml)
    return landing, markers, dt


async def launch_with_recovery(adapter, session, max_restarts=2):
    """稳定冷启动:登录页先一次重启重试(覆盖瞬态失败);仍登录页则返回
    login,由调用方明确报告「会话过期,需人工登录一次」。"""
    trail = []
    for attempt in range(1, max_restarts + 1):
        landing, markers, dt = await launch_once(adapter, session)
        trail.append((attempt, landing, round(dt, 1), markers["EditText"]))
        if landing in ("home", "unknown"):
            return landing, attempt - 1, trail
    return "login", max_restarts - 1, trail


async def main() -> int:
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 10
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
    log: list[str] = []
    try:
        natural = {"home": 0, "login": 0, "unknown": 0}
        recovered = {"home": 0, "login": 0, "unknown": 0}
        restarts_total = 0
        for i in range(1, iterations + 1):
            landing, markers, dt = await launch_once(adapter, session)
            natural[landing] += 1
            line = f"[{i:02d}] 自然落点={landing} ({dt:.1f}s) {markers}"
            if landing == "home":
                final, restarts = "home", 0
            elif landing == "unknown":
                final, restarts = "unknown", 0
            else:
                final, restarts, trail = await launch_with_recovery(adapter, session)
                restarts_total += restarts
                line += f" → 恢复{restarts}次重启 → {final} {trail}"
            recovered[final] += 1
            print(line, flush=True)
            log.append(line)
            if landing == "login":
                xml = await dump_ui(session)
                suffix = "login" if final == "login" else "login_recovered"
                (SNAP / f"coldstart_{i:02d}_{suffix}.xml").write_text(
                    xml, encoding="utf-8"
                )
                (SNAP / f"coldstart_{i:02d}_{suffix}.png").write_bytes(
                    await adapter.screenshot()
                )

        print("\n===== 汇总 =====")
        print(f"自然落点分布: {natural}")
        print(f"恢复分支后:   {recovered} (共自动重启 {restarts_total} 次)")
        # 判定:恢复分支后全部到达首页(unknown 也算失败)
        verdict = recovered["home"] == iterations
        print(f"稳定化结论: {'PASS' if verdict else 'FAIL'}")
        if recovered["login"]:
            print("login 残留:会话过期所致,重启不可恢复;"
                  "需人工登录一次(图形验证码不可自动化),登录后重跑本脚本")
        return 0 if verdict else 1
    finally:
        session.close()
        (SNAP / "coldstart_recovery_summary.txt").write_text(
            "\n".join(log), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
