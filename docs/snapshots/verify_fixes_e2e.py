"""真机 viewer HTTP/WS 端到端验证:审查修复项(2026-10-24 批次)的实机核验。

前置:serve 已在 127.0.0.1:8765 启动(mobile_auto.cli serve),真机在线。
核验项:
  P1-1  来源校验:仿冒回环前缀 403;回环+端口 200;WS 仿冒来源拒握手
  P1-2  跨进程互斥:外部进程持 DeviceLease 时采集 409 DeviceBusy
  P1-5  未建会话设备:/devices、/status 200 且 session=null
  P2-6  预览流:按 ?device= 寻址建立 WS 收帧;断开后可重建(切换设备语义)
  P2-10 检查冻结:采集快照可 /freeze 固定
  P2-11 同名模板:第二次创建 409 DuplicateTemplate;确认更新 PUT 200
用法:python docs/snapshots/verify_fixes_e2e.py
"""

from __future__ import annotations

import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:8765"
DEVICE = "749622c9"
QS = f"?device={DEVICE}"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    http = httpx.Client(base_url=BASE, timeout=30)

    # ── P1-5:任何寻址之前,设备列表不 500 且 session=null ──────────
    res = http.get("/api/v1/devices")
    check("P1-5 /devices 200", res.status_code == 200, f"status={res.status_code}")
    devices = {d["serial"]: d for d in res.json().get("devices", [])}
    check(
        "P1-5 真机在列表且未建会话",
        DEVICE in devices and devices[DEVICE]["session"] is None,
        str(devices.get(DEVICE)),
    )
    res = http.get(f"/api/v1/status{QS}")
    check(
        "P1-5 /status?device 200 且 disconnected",
        res.status_code == 200 and res.json()["device"]["state"] == "disconnected",
        f"status={res.status_code}",
    )

    # ── P1-1:来源校验 ────────────────────────────────────────────
    res = http.get("/api/v1/status", headers={"origin": "http://localhost.attacker.example"})
    check("P1-1 仿冒回环前缀 403", res.status_code == 403, f"status={res.status_code}")
    res = http.get("/api/v1/status", headers={"origin": "http://127.0.0.1:8765"})
    check("P1-1 回环+端口 200", res.status_code == 200, f"status={res.status_code}")

    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import InvalidStatus

    try:
        with ws_connect(
            f"ws://127.0.0.1:8765/api/v1/preview{QS}",
            additional_headers={"Origin": "http://localhost.attacker.example"},
        ) as ws:
            ws.recv(timeout=5)
        check("P1-1 WS 仿冒来源被拒", False, "握手意外成功")
    except InvalidStatus as exc:
        check("P1-1 WS 仿冒来源被拒", True, f"handshake rejected: {exc.response.status_code}")
    except Exception as exc:  # noqa: BLE001
        check("P1-1 WS 仿冒来源被拒", True, f"rejected: {type(exc).__name__}")

    # ── P2-6:按设备寻址的预览流;断开后可重建 ─────────────────────
    def read_preview_frames(label: str) -> str:
        kinds = []
        with ws_connect(f"ws://127.0.0.1:8765/api/v1/preview{QS}") as ws:
            for _ in range(3):
                import json as _json

                frame = _json.loads(ws.recv(timeout=15))
                kinds.append(frame.get("kind"))
        check(f"P2-6 {label} 收到 3 帧", len(kinds) == 3, f"kinds={kinds}")
        return ",".join(kinds)

    read_preview_frames("首次连接")
    time.sleep(0.3)
    read_preview_frames("断开后重连")

    # ── P2-10 + P2-11:采集 → 冻结 → 模板创建/同名 409/确认更新 ──
    res = http.post(f"/api/v1/snapshots{QS}", json={"with_tree": True})
    check("P2-10 采集快照(含树)", res.status_code == 200, f"status={res.status_code}")
    snap = res.json()
    sid = snap["snapshot_id"]
    res = http.post(f"/api/v1/snapshots/{sid}/freeze", json={"frozen": True})
    check(
        "P2-10 /freeze 固定引用",
        res.status_code == 200 and res.json().get("frozen") is True,
        f"status={res.status_code}",
    )
    res = http.get(f"/api/v1/snapshots/{sid}/image")
    check("P2-10 冻结后快照可读", res.status_code == 200, f"status={res.status_code}")

    region = [10, 20, 40, 40]
    import time as _time

    logical = f"e2e/verify-{_time.time_ns() % 10_000_000}.png"
    tpl_body = {"snapshot_id": sid, "logical": logical, "region": region}
    res = http.post(f"/api/v1/templates{QS}", json=tpl_body)
    check("P2-11 首次创建模板 200", res.status_code == 200, f"status={res.status_code}")
    old_sha = res.json().get("sha256", "")
    res = http.post(f"/api/v1/templates{QS}", json=tpl_body)
    check(
        "P2-11 同名创建 409 DuplicateTemplate",
        res.status_code == 409 and res.json().get("code") == "DuplicateTemplate",
        f"status={res.status_code} body={res.json() if res.status_code != 409 else ''}",
    )
    res = http.put(
        f"/api/v1/templates/e2e/verify.png{QS}",
        json={**tpl_body, "expected_old_sha256": old_sha, "region": [60, 80, 40, 40]},
    )
    check("P2-11 确认更新 PUT 200", res.status_code == 200, f"status={res.status_code}")

    # ── P1-2:外部进程持真实文件锁时,采集被 409 门控 ─────────────
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import time\n"
                "from mobile_auto.core.lease import DeviceLease\n"
                "lease = DeviceLease('749622c9')\n"
                "lease.acquire()\n"
                "print('HELD', flush=True)\n"
                "time.sleep(10)\n"
                "lease.release()\n"
                "print('RELEASED', flush=True)\n"
            ),
        ],
        cwd=".",
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        line = holder.stdout.readline().strip()
        check("P1-2 外部进程取得设备租约", line == "HELD", f"stdout={line!r}")
        res = http.post(f"/api/v1/snapshots{QS}", json={"with_tree": False})
        check(
            "P1-2 持锁期间采集 409 DeviceBusy",
            res.status_code == 409 and res.json().get("code") == "DeviceBusy",
            f"status={res.status_code} code={res.json().get('code')}",
        )
        res = http.get(f"/api/v1/status{QS}")
        check(
            "P1-2 status 保持 ready(外部锁不改变本服务会话状态)",
            res.json()["device"]["state"] == "ready"
            and res.json()["device"]["busy"] is False,
            str(res.json()["device"]),
        )
    finally:
        holder.wait(timeout=15)
    time.sleep(0.5)
    res = http.post(f"/api/v1/snapshots{QS}", json={"with_tree": False})
    check("P1-2 释放后采集恢复 200", res.status_code == 200, f"status={res.status_code}")

    http.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
    for name, _, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
