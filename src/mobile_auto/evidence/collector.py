"""失败证据收集、采集失败归因与运行清单(plan 10;GLOSSARY"失败证据")。

证据写入配置的产物目录并登记运行清单;运行清单是网页与工具读取证据的
唯一入口。取证有界:会话 FAULTED 时不调用设备线程,只保存已有材料并
记录"无法采集"原因;等待超时同样放弃,失败用例不因取证挂起。
证据不包含输入文本与凭据(本模块不接收输入值)。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..core.session import DeviceSession, SessionState
from ..errors import SessionFaulted

_CAPTURE_TIMEOUT_SECONDS = 5.0
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z_-]")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _safe_label(label: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", label)[:40]
    return cleaned or "frame"


def collect_failure_evidence(
    session: DeviceSession,
    failure: BaseException | str,
    *,
    operation_frames: Sequence[tuple[str, bytes]] = (),
    timeout: float = _CAPTURE_TIMEOUT_SECONDS,
) -> dict:
    """收集失败现场,写入产物目录并登记运行清单;返回登记条目。

    operation_frames 为操作时使用的画面(调用方在操作过程中采集),
    与失败后新采集的画面分开保存,不互相冒充。本函数尽力采集:
    设备不可用、超时与写入失败归因为"无法采集",不向上抛出非证据错误。
    失败后画面优先取 scrcpy 视频帧(毫秒级,不占 u2 队列,P1 工单 06);
    通道不可用回退 u2 截图,frame_source 如实标注来源。
    """
    config = session.config
    created_at = _utc_now()
    failure_at = time.monotonic()
    evidence_id = f"{uuid.uuid4().hex[:16]}"
    base_dir = Path(config.artifacts_dir) / "evidence" / evidence_id
    base_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict] = []
    not_captured: list[dict] = []

    if isinstance(failure, BaseException):
        failure_info = {"type": type(failure).__name__, "message": str(failure)}
    else:
        failure_info = {"type": "Failure", "message": str(failure)}

    # 操作时画面:调用方提供的既有材料,不发起设备调用
    for label, png in operation_frames:
        name = f"operation-{_safe_label(label)}.png"
        (base_dir / name).write_bytes(png)
        artifacts.append(
            {"name": name, "kind": "operation-frame", "captured_at": created_at}
        )

    # 失败后新采集的画面与 UI 树:有界取证
    frame, tree, screen, reason, frame_source = _capture_post_failure(
        session, timeout, failure_at
    )
    capture_time = _utc_now()
    journal = getattr(session, "journal", None)
    journal_summary = journal.summary() if journal is not None else None
    if reason is not None:
        not_captured.append({"kind": "post-failure", "reason": reason})
    if frame is not None:
        (base_dir / "failure-frame.png").write_bytes(frame)
        artifacts.append(
            {"name": "failure-frame.png", "kind": "failure-frame", "captured_at": capture_time}
        )
    if tree is not None:
        (base_dir / "ui-tree.xml").write_text(tree, encoding="utf-8")
        artifacts.append(
            {"name": "ui-tree.xml", "kind": "ui-tree", "captured_at": capture_time}
        )

    record = {
        "evidence_id": evidence_id,
        "created_at": created_at,
        "failure": failure_info,
        "device": {
            "serial": session.serial,
            "app_package": config.app_package,
            "screen_size": list(screen) if screen else None,
            # 弹窗守卫统计:失败前是否清理过、命中什么;与"是否静默重试"无关
            "dialog_guard": session.dialog_guard_stats,
            # 坐标基准(工单 04):注入时通道/后端两组尺寸与刷新结果;
            # 本会话尚未发生通道注入时为 null
            "coordinate_spaces": _coordinate_spaces(session),
        },
        "frame_source": frame_source,
        "artifacts": artifacts,
        "not_captured": not_captured,
        # 动作流水摘要(工单 01):步数/总耗时/首个失败步;未开启证据时为 null
        "journal_summary": journal_summary,
    }
    (base_dir / "meta.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest_entry = {
        "evidence_id": evidence_id,
        "created_at": created_at,
        "device_serial": session.serial,
        "app_package": config.app_package,
        "failure_type": failure_info["type"],
        "frame_source": frame_source,
        "artifacts": [a["name"] for a in artifacts],
        "not_captured": not_captured,
    }
    manifest_path = Path(config.artifacts_dir) / "run-manifest.jsonl"
    with open(manifest_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
    return record


def _capture_post_failure(session: DeviceSession, timeout: float, failure_at: float):
    """失败后采集(画面、UI 树、屏幕尺寸);返回 (frame, tree, screen,
    reason, frame_source)。

    画面:会话已持有的健康 scrcpy 通道取视频帧(不主动启动通道、
    不占 u2 队列);否则回退 u2 截图。FAULTED 会话不再调用设备线程
    (plan 7.3);提交与等待均有界。frame_source ∈
    {"scrcpy-video", "u2", None};capture_lag 为取证耗时毫秒。
    """
    if session.state == SessionState.FAULTED:
        return None, None, None, "会话 FAULTED,不再调用设备线程;仅保存已有材料", None

    frame = None
    source = None
    channel = session.healthy_channel()
    if channel is not None:
        try:
            jpeg, width, height = channel.latest_frame()
            frame = _jpeg_to_png(jpeg)
            source = "scrcpy-video"
        except Exception:
            frame = None  # 通道取帧失败:回退 u2,不吞掉树采集机会
    if frame is None:
        try:
            future = session.submit(lambda backend: backend.screenshot_png())
        except SessionFaulted as exc:
            return None, None, None, f"提交取证被拒绝:{exc}", None
        try:
            frame = future.result(timeout=timeout)
            source = "u2"
        except Exception as exc:
            future.cancel()
            return None, None, None, f"取证未完成({type(exc).__name__}),已放弃等待", None

    tree = None
    screen = None
    try:
        future = session.submit(
            lambda backend: (backend.dump_ui(), backend.screen_size())
        )
        tree, screen = future.result(timeout=timeout)
    except Exception as exc:
        reason = f"UI 树取证未完成({type(exc).__name__})"
        tree, screen = None, None
    else:
        reason = None

    capture_lag_ms = round((time.monotonic() - failure_at) * 1000, 1)
    return frame, tree, screen, reason, {"source": source, "capture_lag_ms": capture_lag_ms}


def _coordinate_spaces(session: DeviceSession) -> dict | None:
    """会话最近一次注入前的基准快照;无通道注入记录时为 None。"""
    basis = getattr(session, "last_coordinate_basis", None)
    if not isinstance(basis, dict):
        return None
    copied = dict(basis)
    for key in ("channel", "backend"):
        value = copied.get(key)
        copied[key] = list(value) if value else None
    return copied


def _jpeg_to_png(jpeg: bytes) -> bytes | None:
    import cv2
    import numpy as np

    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        return None
    ok, buf = cv2.imencode(".png", decoded)
    return buf.tobytes() if ok else None


# ── 运行清单:证据读取的唯一入口(plan 8.2)────────────────────


def read_manifest(artifacts_dir: str | Path) -> list[dict]:
    """读取运行清单;损坏行跳过,不视为错误。"""
    path = Path(artifacts_dir) / "run-manifest.jsonl"
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def read_evidence_file(artifacts_dir: str | Path, entry: dict, name: str) -> bytes:
    """按清单登记项读取证据文件;未登记的名称拒绝(唯一读取入口)。"""
    if name not in entry.get("artifacts", []):
        raise FileNotFoundError(f"证据文件未在运行清单登记:{name!r}")
    evidence_id = entry["evidence_id"]
    if not re.fullmatch(r"[0-9a-f]{16}", evidence_id):
        raise ValueError(f"非法证据标识:{evidence_id!r}")
    path = Path(artifacts_dir) / "evidence" / evidence_id / name
    return path.read_bytes()
