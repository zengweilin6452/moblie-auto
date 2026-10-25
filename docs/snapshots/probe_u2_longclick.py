"""前置探针:u2 d.long_click 在本机(MIUI/Android14)是否触发真长按。

判据:Beta_Academy 首页搜索框(EditText)长按 0.8s 后 dump 出现
粘贴/全选/选择 工具条;同点普通 click 不应出现。
同时验证 u2 touch.up 可用性(scrcpy 中断恢复路径的依赖)。
"""
import re
import sys
import time
from pathlib import Path

import uiautomator2 as u2

sys.path.insert(0, r"D:\workspace\claudecode-test\moblie-auto\src")

SERIAL = "749622c9"
SNAP = Path(r"D:\workspace\claudecode-test\moblie-auto\docs\snapshots")


def markers(xml: str) -> list[str]:
    found = []
    for kw in ("粘贴", "Paste", "全选", "Select all", "选择", "剪切", "Cut", "分享"):
        if kw in xml:
            found.append(kw)
    return found


def main() -> int:
    d = u2.connect(SERIAL)
    d.app_start("com.sinosoft.trainapp", ".MainActivity", stop=True)
    deadline = time.time() + 15
    while time.time() < deadline:
        if d(className="android.widget.EditText").exists:
            break
        time.sleep(0.5)
    else:
        print("FAIL: 首页搜索框未出现")
        return 1
    bounds = d(className="android.widget.EditText").bounds()  # (l, t, r, b)
    cx, cy = (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
    print(f"目标 EditText bounds={bounds} center=({cx},{cy})")

    # 对照组:普通 click(聚焦但无长按菜单)
    d.click(cx, cy)
    time.sleep(1.0)
    base_xml = d.dump_hierarchy()
    (SNAP / "probe_longclick_control.xml").write_text(base_xml, encoding="utf-8")
    base_markers = markers(base_xml)
    d.press("back")
    time.sleep(0.8)

    # 实验组:long_click 0.8s
    d.long_click(cx, cy, duration=0.8)
    time.sleep(1.2)
    xml = d.dump_hierarchy()
    (SNAP / "probe_longclick_after.xml").write_text(xml, encoding="utf-8")
    m = markers(xml)
    print(f"普通 click 后标记: {base_markers or '无'}")
    print(f"long_click 后标记: {m or '无'}")
    ok = bool(m) and not base_markers
    print(f"结论: {'PASS: u2 long_click 触发长按菜单' if ok else 'FAIL: 未触发长按菜单'}")

    # touch.up 可用性(down→up 空按,不产生点击副作用可能性低;在键盘外区域)
    d.press("back")
    time.sleep(0.5)
    try:
        d.touch.down(cx, cy)
        time.sleep(0.05)
        d.touch.up(cx, cy)
        print("PASS: u2 touch.down/up 注入无报错")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: u2 touch.up 报错 {exc!r}")
        ok = False
    d.press("home")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
