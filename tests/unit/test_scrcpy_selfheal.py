"""scrcpy 残留 server 自愈单元测试(P2 工单 02):伪造 adb/wetest 层,不碰设备。"""

from __future__ import annotations

import socket
import threading

import pytest

wetest_scrcpy = pytest.importorskip("wetest.scrcpy", reason="stream extra 未安装")

from mobile_auto.errors import StreamUnavailable  # noqa: E402
from mobile_auto.stream import channel as scrcpy_channel  # noqa: E402
from mobile_auto.stream import selfheal  # noqa: E402
from mobile_auto.stream.channel import (  # noqa: E402
    HealReport,
    ScrcpyChannel,
    _kill_stale_servers,
    _parse_server_listing,
)

SCREEN_W, SCREEN_H = 1080, 2400
OWN_MOD = None  # 惰性取当前进程模值(模块加载时 getpid 已定,但测试内取更直观)


def own_mod() -> int:
    import os

    return os.getpid() % 100_000


class FakeStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeSync:
    def __init__(self, pushes):
        self.pushes = pushes

    def push(self, src, dst):
        self.pushes.append((src, dst))


class HealAdbDevice:
    """可编程 shell:残留扫描/kill/wm size/app_process 流启动。

    servers: {设备端 pid: socket 前缀};kill 成功即从表中移除,并回调
    on_kill(用于模拟"编码器被释放后连接恢复")。
    """

    def __init__(self, servers=None, *, scan_error=False, on_kill=None):
        self.servers = dict(servers or {})
        self.scan_error = scan_error
        self.on_kill = on_kill
        self.kills: list[int] = []
        self.launches = 0
        self.scans = 0
        self.pushes: list[tuple[str, str]] = []
        self.sync = FakeSync(self.pushes)

    def _listing(self) -> str:
        return "\n".join(
            f"/proc/{pid}/comm:{prefix}" for pid, prefix in sorted(self.servers.items())
        )

    def _upstream_listing(self) -> str:
        """上游 server 条目:cmdline 行(scid 形式;实证 app_process comm='main')。"""
        lines = []
        for pid, prefix in sorted(self.servers.items()):
            if prefix.startswith("scrcpy_"):
                scid = prefix.removeprefix("scrcpy_")
                lines.append(
                    f"{pid} app_process / com.genymobile.scrcpy.Server 5.0.1 "
                    f"scid={scid} log_level=info video=true"
                )
        return "\n".join(lines)

    def shell(self, cmd, stream=False):
        if stream:
            self.launches += 1
            return FakeStream()
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        if "comm" in cmd and "grep" in cmd:
            self.scans += 1
            if self.scan_error:
                raise RuntimeError("adb 探测失败")
            return self._listing()
        if "pgrep" in cmd and "genymobile" in cmd:
            self.scans += 1
            if self.scan_error:
                raise RuntimeError("adb 探测失败")
            return self._upstream_listing()
        if cmd.startswith("kill "):
            pid = int(cmd.split()[1])
            self.kills.append(pid)
            self.servers.pop(pid, None)
            if self.on_kill is not None:
                self.on_kill()
            return ""
        if cmd == "wm size":
            return f"Physical size: {SCREEN_W}x{SCREEN_H}\nOverride size: none\n"
        return ""


class OkCtrl:
    def __init__(self, device=None, timeout=5):
        self._sock = socket.socket()

    def write(self, data):
        return len(data)

    def get_rotation(self):
        return 0

    def close(self):
        self._sock.close()


class OkVideo:
    def __init__(self, device=None, timeout=5):
        self._thread = None
        self._release = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._release.wait, daemon=True)
        self._thread.start()

    def close(self):
        self._release.set()
        self._thread = None


@pytest.fixture
def clean_live_prefixes():
    scrcpy_channel._LIVE_PREFIXES.clear()
    yield scrcpy_channel._LIVE_PREFIXES
    scrcpy_channel._LIVE_PREFIXES.clear()


def patch_wetest(monkeypatch, adb, *, ctrl_fails_while=None):
    """注入伪造 adb 与 wetest;ctrl 在 ctrl_fails_while() 为真时连接失败。"""
    import adbutils

    state = {"busy": ctrl_fails_while is not None}
    monkeypatch.setattr(adbutils.adb, "device", lambda serial: adb)

    def make_ctrl(device=None, timeout=5):
        if state["busy"] and (ctrl_fails_while is None or ctrl_fails_while(adb)):
            raise ConnectionError("server 未就绪(模拟编码器占用)")
        return OkCtrl(device, timeout)

    monkeypatch.setattr("wetest.scrcpy.CtrlConnection", make_ctrl)
    monkeypatch.setattr("wetest.scrcpy.VideoStreamScreenshot", OkVideo)
    monkeypatch.setattr("wetest.scrcpy.AndroidDevice", lambda **kw: object())
    return state


# ── 判定与解析 ─────────────────────────────────────────────


def test_parse_server_listing_extracts_pid_and_prefix():
    listing = "/proc/111/comm:ma-99999-3\n/proc/222/comm:sh\n"
    assert _parse_server_listing(listing) == [(111, 99999, "ma-99999-3")]


def test_heal_kills_only_dead_host_servers(clean_live_prefixes):
    clean_live_prefixes.add(f"ma-{own_mod()}-1")  # 本进程活跃通道
    adb = HealAdbDevice(
        {101: "ma-99999-1", 102: "ma-4242-1", 103: f"ma-{own_mod()}-1"}
    )
    report = _kill_stale_servers(
        adb, serial="S", own_mod_pid=own_mod(), alive_pids={4242, own_mod()}
    )
    assert report == HealReport(killed=1, skipped_alive=1, skipped_own=1, note=report.note)
    assert adb.kills == [101]  # 只杀宿主已死的 99999


def test_heal_normal_mode_spares_own_leftover_for_collision_safety(clean_live_prefixes):
    """正常模式下本进程遗留(不在活跃表)也不杀:pid 取模碰撞时无法与
    他人活跃 server 区分,宁漏勿错(留待手动清理)。"""
    adb = HealAdbDevice({111: f"ma-{own_mod()}-8"})  # 本进程模值,不在活跃表
    report = _kill_stale_servers(
        adb, serial="S", own_mod_pid=own_mod(), alive_pids={own_mod()}
    )
    assert report.killed == 0 and report.skipped_own == 1
    assert adb.kills == []


def test_heal_degraded_mode_only_own_leftover(clean_live_prefixes, monkeypatch):
    monkeypatch.setattr(selfheal, "alive_host_mod_pids", lambda: None)
    clean_live_prefixes.add(f"ma-{own_mod()}-3")  # 本进程活跃通道
    adb = HealAdbDevice(
        {
            201: f"ma-{own_mod()}-9",  # 本进程遗留(不在活跃表)
            202: f"ma-{own_mod()}-3",  # 本进程活跃
            203: "ma-88888-1",  # 他人:降级下无法判定
        }
    )
    report = _kill_stale_servers(adb, serial="S", own_mod_pid=own_mod())
    assert report.killed == 1
    assert adb.kills == [201]
    assert "降级" in report.note


def test_heal_scan_failure_reports_manual_hint(clean_live_prefixes):
    adb = HealAdbDevice(scan_error=True)
    report = _kill_stale_servers(adb, serial="S", own_mod_pid=own_mod())
    assert report.killed == 0
    assert "手动清理" in report.note and "pkill" in report.note


def test_heal_empty_listing_reports_manual_hint(clean_live_prefixes):
    adb = HealAdbDevice()
    report = _kill_stale_servers(adb, serial="S", own_mod_pid=own_mod())
    assert report.killed == 0
    assert "手动清理" in report.note


# ── start() 自愈路径 ───────────────────────────────────────


def test_start_heals_dead_residue_then_recovers(monkeypatch, clean_live_prefixes):
    adb = HealAdbDevice({301: "ma-77777-1"})
    adb.on_kill = lambda: None
    monkeypatch.setattr(
        selfheal, "alive_host_mod_pids", lambda: {own_mod()}
    )
    patch_wetest(monkeypatch, adb, ctrl_fails_while=lambda a: bool(a.servers))
    channel = ScrcpyChannel("SERIAL")
    channel.start(timeout=0.4)  # 首连失败 → 清 301 → 重试成功
    assert channel.started
    assert adb.kills == [301]
    assert adb.launches == 2  # 重试恰好一次
    channel.stop()


def test_start_without_healable_residue_raises_with_hint(
    monkeypatch, clean_live_prefixes
):
    adb = HealAdbDevice()  # 设备端无残留
    monkeypatch.setattr(
        selfheal, "alive_host_mod_pids", lambda: {own_mod()}
    )
    patch_wetest(monkeypatch, adb, ctrl_fails_while=lambda a: True)
    channel = ScrcpyChannel("SERIAL")
    with pytest.raises(StreamUnavailable, match="残留自愈未清得任何 server.*pkill"):
        channel.start(timeout=0.4)
    assert adb.launches == 1  # 无可清残留:不重试


def test_start_never_kills_live_channel_of_same_process(
    monkeypatch, clean_live_prefixes
):
    # 通道 A 正常启动(占住本进程活跃表)
    ok_adb = HealAdbDevice()
    state = patch_wetest(monkeypatch, ok_adb)
    channel_a = ScrcpyChannel("SERIAL")
    channel_a.start(timeout=0.4)
    live_prefix = channel_a._sock_prefix

    # 通道 B 启动失败,设备端同时存在 A(活跃)与宿主已死残留
    residue_adb = HealAdbDevice({401: "ma-66666-1", 402: live_prefix})
    residue_adb.on_kill = lambda: state.update(busy=False)
    import adbutils

    monkeypatch.setattr(adbutils.adb, "device", lambda serial: residue_adb)
    monkeypatch.setattr(
        selfheal, "alive_host_mod_pids", lambda: {own_mod()}
    )
    state["busy"] = True
    channel_b = ScrcpyChannel("SERIAL")
    channel_b.start(timeout=0.4)
    assert residue_adb.kills == [401]  # 只杀宿主已死的;A 的前缀不动
    assert channel_a.started and channel_a.healthy()
    channel_b.stop()
    channel_a.stop()


# ── 上游 server(scid)残留与双前缀共存(P2 上游迁移)───────


def test_heal_kills_dead_upstream_server(clean_live_prefixes, monkeypatch):
    monkeypatch.setattr(
        selfheal, "alive_host_mod_pids", lambda: {own_mod()}
    )
    adb = HealAdbDevice({501: "scrcpy_0000dead"})
    report = scrcpy_channel._kill_stale_servers(
        adb, serial="S", own_mod_pid=own_mod()
    )
    assert report.killed == 1 and adb.kills == [501]


def test_heal_spares_live_upstream_channel(clean_live_prefixes, monkeypatch):
    live_scid = f"scrcpy_{(own_mod() << 8) | 7:08x}"
    clean_live_prefixes.add(live_scid)  # 本进程活跃上游通道
    monkeypatch.setattr(
        selfheal, "alive_host_mod_pids", lambda: {own_mod()}
    )
    adb = HealAdbDevice({502: live_scid})
    report = scrcpy_channel._kill_stale_servers(
        adb, serial="S", own_mod_pid=own_mod()
    )
    assert report.killed == 0 and adb.kills == []
    assert report.skipped_own == 1


def test_heal_mixed_backends_in_one_scan(clean_live_prefixes, monkeypatch):
    """wetest comm 残留与 upstream scid 残留同场:只杀宿主已死的。"""
    monkeypatch.setattr(
        selfheal, "alive_host_mod_pids", lambda: {own_mod(), 4242}
    )
    adb = HealAdbDevice(
        {
            601: "ma-99999-1",       # wetest 残留(宿主死)→ 杀
            602: "ma-4242-1",        # wetest 他进程存活 → 跳过
            603: "scrcpy_0000beef",  # upstream 残留(宿主 0xbeef 死)→ 杀
        }
    )
    report = scrcpy_channel._kill_stale_servers(
        adb, serial="S", own_mod_pid=own_mod()
    )
    assert report.killed == 2 and sorted(adb.kills) == [601, 603]
    assert report.skipped_alive == 1
