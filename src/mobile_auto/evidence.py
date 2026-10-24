"""失败证据收集、采集失败归因与运行清单(plan 10;GLOSSARY"失败证据")。

证据写入配置的产物目录并登记运行清单;运行清单是网页与工具读取证据的
唯一入口。取证有界:会话 FAULTED 时不调用设备线程,只保存已有材料并
记录"无法采集"原因;等待超时同样放弃,失败用例不因取证挂起。
证据不包含输入文本与凭据(本模块不接收输入值)。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .errors import SessionFaulted
from .session import DeviceSession, SessionState

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
    """
    config = session.config
    created_at = _utc_now()
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
    frame, tree, screen, reason = _capture_post_failure(session, timeout)
    capture_time = _utc_now()
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
        },
        "artifacts": artifacts,
        "not_captured": not_captured,
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
        "artifacts": [a["name"] for a in artifacts],
        "not_captured": not_captured,
    }
    manifest_path = Path(config.artifacts_dir) / "run-manifest.jsonl"
    with open(manifest_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
    return record


def _capture_post_failure(session: DeviceSession, timeout: float):
    """失败后采集(截图、UI 树、屏幕尺寸);返回 (frame, tree, screen, reason)。

    FAULTED 会话不再调用设备线程(plan 7.3);提交与等待均有界。
    """
    if session.state == SessionState.FAULTED:
        return None, None, None, "会话 FAULTED,不再调用设备线程;仅保存已有材料"
    try:
        future = session.submit(
            lambda backend: (
                backend.screenshot_png(),
                backend.dump_ui(),
                backend.screen_size(),
            )
        )
    except SessionFaulted as exc:
        return None, None, None, f"提交取证被拒绝:{exc}"
    try:
        frame, tree, screen = future.result(timeout=timeout)
        return frame, tree, screen, None
    except Exception as exc:  # 超时或执行失败:放弃等待,留因不挂起
        future.cancel()
        return None, None, None, f"取证未完成({type(exc).__name__}),已放弃等待"


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
