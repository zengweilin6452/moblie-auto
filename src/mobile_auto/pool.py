"""设备池:以跨进程设备租约为协调器的多设备获取(P2 工单 05)。

不建中心服务:每个 worker 枚举池内已授权在线设备,非阻塞试获取租约,
抢到即绑定;全忙按固定节奏重试至超时报 DeviceBusy,不静默使用池外设备
(与 resolve_serial 语义一致)。预获取的租约经 lease_factory 注入会话
(DeviceLease.acquire 幂等,会话 start 复用同一租约,消除"获取-启动"
间隙里被他人抢走的竞争窗口)。

运行状态文件(供 viewer 占用/运行态模块,工单 07 消费):池化运行期间
由本模块写 artifacts_dir/runs/<worker>.json,单写者无锁;读方容忍
半写与心跳过期。
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from .core.backend import enumerate_device_states
from .core.lease import DeviceLease
from .errors import DeviceBusy, DeviceUnavailable

POOL_WAIT_SECONDS = 60.0  # 全忙时的获取超时
POOL_POLL_INTERVAL = 1.0
RUN_STATE_STALE_SECONDS = 30.0  # 心跳超阈视为失联(读方判定)


def acquire_from_pool(
    pool: tuple[str, ...] | list[str],
    *,
    worker: str = "",
    wait_seconds: float = POOL_WAIT_SECONDS,
    poll_interval: float = POOL_POLL_INTERVAL,
    enumerator=None,
    lease_factory=None,
) -> tuple[str, DeviceLease]:
    """从池内获取一台设备;返回 (serial, 已持有租约)。

    顺序尝试池内已授权在线设备,非阻塞试锁,抢到即返回;全忙/全不在线
    按 poll_interval 重试至 wait_seconds 超时报 DeviceBusy(信息含在线
    设备列表,便于区分"被占用"与"不在线")。
    """
    pool = tuple(dict.fromkeys(pool))  # 去重保序
    if not pool:
        raise DeviceBusy("设备池为空;用 --mobile-devices 或配置 device_pool 指定")
    enumerator = enumerator or enumerate_device_states
    lease_factory = lease_factory or (
        lambda serial: DeviceLease(serial, worker=worker)
    )
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            states = enumerator()
        except DeviceUnavailable as exc:
            raise DeviceUnavailable(f"设备池枚举失败:{exc}") from exc
        authorized = [s for s in pool if states.get(s) == "device"]
        for serial in authorized:
            lease = lease_factory(serial)
            try:
                lease.acquire()
            except DeviceBusy:
                continue  # 被其他 worker 持有:试下一台
            return serial, lease
        if time.monotonic() >= deadline:
            raise DeviceBusy(
                f"设备池在 {wait_seconds:.0f}s 内无可获取设备;"
                f"在线:{authorized or '无'};池:{list(pool)}"
            )
        time.sleep(poll_interval)


def worker_artifacts_dir(base: Path, worker: str) -> Path:
    """xdist worker 各自独立的产物目录;非 xdist(main)保持原目录。"""
    base = Path(base)
    return base / f"xdist-{worker}" if worker else base


def write_run_state(
    runs_dir: Path,
    *,
    worker: str,
    phase: str,
    serial: str | None = None,
    current_test: str | None = None,
) -> None:
    """覆写本 worker 的运行状态文件;失败静默(展示性数据不阻断测试)。"""
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "worker": worker or "main",
            "phase": phase,  # started / running / finished
            "serial": serial,
            "current_test": current_test,
            "pid": os.getpid(),
            "heartbeat": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        path = runs_dir / f"{worker or 'main'}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)  # 原子替换:读方不会看到半写
    except OSError:
        pass


def read_run_states(runs_dir: Path) -> list[dict]:
    """读取全部运行状态文件并标注失联;坏文件跳过,不抛错。"""
    result = []
    try:
        paths = sorted(Path(runs_dir).glob("*.json"))
    except OSError:
        return result
    now = datetime.now(UTC)
    for path in paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            heartbeat = datetime.fromisoformat(state["heartbeat"])
            stale = (now - heartbeat).total_seconds() > RUN_STATE_STALE_SECONDS
        except (KeyError, ValueError):
            stale = True
        state["stale"] = stale
        result.append(state)
    return result
