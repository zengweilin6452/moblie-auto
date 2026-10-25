"""设备端残留 server 自愈:包级共享能力(P2 工单 02;上游迁移接线)。

启动失败时枚举设备端本包 server(两类前缀并存),判定宿主存活,kill
宿主已死者后由调用方重试一次;无可清残留则如实报错并附手动清理指引。

双前缀扫描(实证驱动):
- wetest server:进程名(comm)被设为 socket 前缀 ma-<宿主pid模>-<序号>,
  一次 grep /proc/*/comm 枚举(java 进程 cmdline 不含启动参数,cmdline
  方案在 wetest 上不可行);
- 上游 server(v5.0.1):app_process 本体 comm 被改写为 'main',cmdline
  保留完整参数含 scid=<8hex>(sh 壳与本体都携带,只登记本体命中);
  scid 高 23 位编码宿主 pid 模值,socket 名即 scrcpy_<scid>。

fail-safe 判定:正常模式(宿主存活枚举可用)只杀"宿主 pid 模值不在
本机存活集"者;降级模式(tasklist 失败)只杀"本进程前缀且不在活跃表";
活跃表(_LIVE_PREFIXES)由各通道实现 start 成功后登记、teardown 移除,
保证同进程 driver/viewer 合法多通道永不被误杀。pid 取模碰撞/pid 复用
的最坏结果是漏清(附手动指引),不会误杀任何可能活跃的通道。
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass

HOST_PID_MOD = 100_000  # socket 前缀/scid 里宿主 pid 的取模口径
# wetest 残留扫描:comm 匹配无 shell 自匹配/引号风险
SCAN_CMD = "grep -H -E '^ma-[0-9]+-[0-9]+$' /proc/[0-9]*/comm"
SERVER_RE = re.compile(r"/proc/(\d+)/comm:ma-(\d+)-(\d+)")
# 上游残留扫描:方括号写法防 pgrep 匹配扫描 shell 自身;tr 的 NUL 用
# 八进制 \000 转义——源码里写 '\0' 会把字面 NUL 字节放进命令串,adbutils
# 传输时引号断裂(真机实证 "no closing quote";fake adb 按子串匹配测不出)
UPSTREAM_SCAN_CMD = (
    "for p in $(pgrep -f 'com[.]genymobile[.]scrcpy[.]Server'); do "
    "echo $p $(cat /proc/$p/cmdline 2>/dev/null | tr '\\000' ' '); done"
)
UPSTREAM_SCID_RE = re.compile(r"scid=([0-9a-f]{8})")

# 本进程已启动未回收的前缀(wetest ma-* / upstream scrcpy_*):同进程
# driver/viewer 双通道共存时,自愈永不杀活跃表内的通道
LIVE_PREFIXES: set[str] = set()
LIVE_PREFIXES_LOCK = threading.Lock()


@dataclass(frozen=True)
class HealReport:
    """一次残留自愈的结果;killed=0 时 note 说明原因并附手动清理指引。"""

    killed: int
    skipped_alive: int
    skipped_own: int
    note: str


def alive_host_mod_pids() -> set[int] | None:
    """本机存活进程 pid 的模值集合(与 socket 前缀同口径);失败返回 None。

    空集与失败同义(按降级处理):把"没有进程存活"当真会把全部 server
    误判为残留。
    """
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        fields = line.split('","')
        if len(fields) < 2:
            continue
        try:
            pids.add(int(fields[1].rstrip('"')) % HOST_PID_MOD)
        except ValueError:
            continue
    return pids or None


def parse_server_listing(output: str) -> list[tuple[int, int, str]]:
    """解析 comm 扫描输出 → [(设备端 pid, 宿主 pid 模值, socket 前缀)]。"""
    servers = []
    for match in SERVER_RE.finditer(output):
        device_pid, mod_pid, seq = match.groups()
        servers.append((int(device_pid), int(mod_pid), f"ma-{mod_pid}-{seq}"))
    return servers


def parse_upstream_listing(output: str) -> list[tuple[int, int, str]]:
    """解析上游 server 扫描 → [(pid, 宿主 pid 模值, scid socket 名)]。

    cmdline 实证(app_process 本体 comm 被改写为 'main'):sh 壳与本体都
    含 scid=;只登记本体的 pgrep 命中(壳随本体退出),socket 名即
    scrcpy_<scid>(活跃表语义对齐 wetest 前缀)。
    """
    servers = []
    for line in (output or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        match = UPSTREAM_SCID_RE.search(parts[1])
        if not match:
            continue
        scid = int(match.group(1), 16)
        prefix = f"scrcpy_{match.group(1)}"
        servers.append((int(parts[0]), scid >> 8, prefix))
    return servers


def kill_stale_servers(
    adb_device, *, serial: str, own_mod_pid: int, alive_pids=None
) -> HealReport:
    """清理设备端残留 server(双前缀);只杀宿主已死的,宁漏勿错。

    正常模式(宿主存活枚举可用):宿主 pid 模值不在存活集 → 残留,杀;
    降级模式(枚举失败):只认"本进程前缀且不在活跃表"的 server,其余
    全部跳过。
    """
    hint = (
        f"adb -s {serial} shell pkill -f 'udt_socket_name=ma-'; "
        f"adb -s {serial} shell pkill -f 'com.genymobile.scrcpy.Server'"
    )
    try:
        listing = adb_device.shell(SCAN_CMD)
        upstream_listing = adb_device.shell(UPSTREAM_SCAN_CMD)
    except Exception as exc:
        return HealReport(0, 0, 0, f"设备端残留扫描失败:{exc};手动清理:{hint}")
    servers = parse_server_listing(listing or "") + parse_upstream_listing(
        upstream_listing or ""
    )
    if not servers:
        return HealReport(0, 0, 0, f"设备端无本包残留 server;如仍失败请手动清理:{hint}")
    if alive_pids is None:
        alive_pids = alive_host_mod_pids()
    degraded = alive_pids is None
    killed = skipped_alive = skipped_own = kill_failed = 0
    for device_pid, mod_pid, prefix in servers:
        with LIVE_PREFIXES_LOCK:
            live = prefix in LIVE_PREFIXES
        if live or (not degraded and mod_pid == own_mod_pid):
            # 活跃表内(同进程合法通道);正常模式下本进程遗留前缀同样
            # 不杀——pid 取模碰撞时无法与他人的活跃 server 区分
            skipped_own += 1
            continue
        stale = mod_pid == own_mod_pid if degraded else mod_pid not in alive_pids
        if not stale:
            skipped_alive += 1
            continue
        try:
            adb_device.shell(f"kill {device_pid}")
            killed += 1
        except Exception:
            kill_failed += 1
    parts = [f"清理残留 {killed} 个", f"跳过 存活={skipped_alive} 本进程={skipped_own}"]
    if degraded:
        parts.append("宿主存活枚举失败,降级仅清本进程遗留")
    if kill_failed:
        parts.append(f"kill 失败 {kill_failed} 个")
    note = ";".join(parts)
    if killed == 0:
        note += f";如仍失败请手动清理:{hint}"
    return HealReport(killed, skipped_alive, skipped_own, note)


__all__ = [
    "HealReport",
    "LIVE_PREFIXES",
    "LIVE_PREFIXES_LOCK",
    "alive_host_mod_pids",
    "kill_stale_servers",
]
