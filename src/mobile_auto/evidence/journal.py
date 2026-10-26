"""动作流水(Action Journal):公共调用边界的一行一记录(契约外观测层)。

落盘位置由调用方给定:

- pytest 侧跟随 ``--mobile-evidence``,与证据同源:
  ``<artifacts>/xdist-<worker>/actions.jsonl``,非 xdist 为
  ``<artifacts>/actions.jsonl``(复用 ``pool.worker_artifacts_dir``);
- viewer 侧始终写 ``<artifacts>/viewer/<serial>/actions.jsonl``。

记录点在适配器公共方法边界(``journaled`` 装饰器),不在设备队列内部:
一次 ``wait_for`` 只一行并含轮询总耗时。嵌套公共调用(``wait_for`` 内部
的 ``is_visible``、``back`` 内部的 ``press_key``)用 contextvars 标记抑制,
保证一次公共调用只写一行。

本模块只是观测层:写失败吞掉并一次性告警,绝不改变返回值与异常类型;
输入文本只记长度、``get_text`` 不记返回值、``goto`` 只记 scheme,避免把
业务内容带进日志。单文件上限 32 MiB,超限追加一行截断标记后停止写入。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contracts import Target

logger = logging.getLogger("mobile_auto.journal")

JOURNAL_FILE_NAME = "actions.jsonl"
MAX_BYTES = 32 * 1024 * 1024
_TRUNCATED_LINE = '{"op":"journal","outcome":"truncated"}\n'

# 同一次公共调用内的嵌套调用标记;contextvars 对 async 任务与线程都成立
_ACTIVE = contextvars.ContextVar("mobile_auto_journal_active", default=False)

_TARGET_KEYS = ("by", "value", "index", "confidence", "region")
# 注入类操作才带 channel;读取类不臆造 "u2"
_INJECTION_OPS = frozenset(
    {
        "click",
        "back",
        "press_key",
        "scroll",
        "device.tap",
        "device.swipe",
        "device.long_press",
    }
)
_NOT_FOUND_OPS = frozenset({"wait_for", "wait_gone"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Journal:
    """单个流水文件:懒创建目录、复用句柄、按行追加(线程安全)。"""

    def __init__(
        self, artifacts_dir: str | Path, session_id: str, *, max_bytes: int = MAX_BYTES
    ) -> None:
        self._dir = Path(artifacts_dir)
        self._session_id = session_id
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._handle = None
        self._truncated = False
        self._disabled = False
        self._warned = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def path(self) -> Path:
        return self._dir / JOURNAL_FILE_NAME

    def record(
        self,
        op: str,
        *,
        target: dict | None = None,
        duration_ms: float,
        outcome: str,
        error_type: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """追加一行;任何失败都被吞掉(观测层不改变操作语义)。"""
        try:
            payload = self._serialize(op, target, duration_ms, outcome, error_type, extra)
        except (TypeError, ValueError):
            self._warn("动作流水字段无法序列化,已停止记录")
            return
        with self._lock:
            if self._truncated or self._disabled:
                return
            handle = self._open()
            if handle is None:
                return
            try:
                size = os.fstat(handle.fileno()).st_size
                if size + len(payload.encode("utf-8")) > self._max_bytes:
                    handle.write(_TRUNCATED_LINE)
                    handle.flush()
                    self._truncated = True
                    self._close_locked()
                    return
                handle.write(payload)
                handle.flush()
            except OSError as exc:
                self._close_locked()
                self._warn("动作流水写入失败(%s),已停止记录", type(exc).__name__)

    def summary(self) -> dict | None:
        """读取本文件的步数/总耗时/首个失败步;文件不存在返回 None。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        steps = 0
        total = 0.0
        first: dict | None = None
        truncated = False
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if line.get("op") == "journal":
                truncated = truncated or line.get("outcome") == "truncated"
                continue
            steps += 1
            try:
                total += float(line.get("duration_ms") or 0.0)
            except (TypeError, ValueError):
                pass
            if first is None and line.get("outcome") != "ok":
                first = {
                    "step": steps,
                    "op": line.get("op"),
                    "outcome": line.get("outcome"),
                    "error_type": line.get("error_type"),
                    "target": line.get("target"),
                }
        if steps == 0 and not truncated:
            return None
        return {
            "steps": steps,
            "total_duration_ms": round(total, 1),
            "first_failure": first,
            "truncated": truncated,
        }

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    # ── 内部 ─────────────────────────────────────────────────

    def _serialize(
        self,
        op: str,
        target: dict | None,
        duration_ms: float,
        outcome: str,
        error_type: str | None,
        extra: dict | None,
    ) -> str:
        record: dict[str, Any] = dict(extra or {})
        record.update(
            {
                "ts": _utc_now(),
                "session": self._session_id,
                "op": op,
                "duration_ms": round(float(duration_ms), 1),
                "outcome": outcome,
            }
        )
        if target:
            record["target"] = {
                key: target[key] for key in _TARGET_KEYS if key in target
            }
        if error_type:
            record["error_type"] = error_type
        return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    def _open(self):
        if self._handle is not None:
            return self._handle
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._handle = open(self.path, "a", encoding="utf-8", newline="\n")
        except OSError as exc:
            self._disabled = True
            self._warn("动作流水目录不可写(%s),已禁用:%s", type(exc).__name__, self._dir)
            return None
        return self._handle

    def _close_locked(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        except OSError:
            pass
        self._handle = None

    def _warn(self, message: str, *args: Any) -> None:
        if self._warned:
            return
        self._warned = True
        logger.warning(message, *args)


def journaled(op: str):
    """公共方法装饰器:记录一行动作流水,同时支持 async/sync。

    只观察:返回值、异常类型与取消语义原样透传;未挂接 Journal 时零开销。
    """

    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(self, *args, **kwargs):
                session = _session_of(self)
                journal = getattr(session, "journal", None) if session is not None else None
                if journal is None or _ACTIVE.get():
                    return await function(self, *args, **kwargs)
                session.begin_operation()
                token = _ACTIVE.set(True)
                started = time.monotonic()
                try:
                    result = await function(self, *args, **kwargs)
                except asyncio.CancelledError:
                    _record(journal, session, op, args, kwargs, started, "unknown")
                    raise
                except Exception as exc:
                    _record(
                        journal,
                        session,
                        op,
                        args,
                        kwargs,
                        started,
                        "error",
                        error_type=type(exc).__name__,
                    )
                    raise
                else:
                    outcome = (
                        "not_found"
                        if op in _NOT_FOUND_OPS and result is False
                        else "ok"
                    )
                    _record(journal, session, op, args, kwargs, started, outcome)
                    return result
                finally:
                    _ACTIVE.reset(token)

            return async_wrapper

        @functools.wraps(function)
        def sync_wrapper(self, *args, **kwargs):
            session = _session_of(self)
            journal = getattr(session, "journal", None) if session is not None else None
            if journal is None or _ACTIVE.get():
                return function(self, *args, **kwargs)
            session.begin_operation()
            token = _ACTIVE.set(True)
            started = time.monotonic()
            try:
                result = function(self, *args, **kwargs)
            except Exception as exc:
                _record(
                    journal,
                    session,
                    op,
                    args,
                    kwargs,
                    started,
                    "error",
                    error_type=type(exc).__name__,
                )
                raise
            else:
                outcome = (
                    "not_found" if op in _NOT_FOUND_OPS and result is False else "ok"
                )
                _record(journal, session, op, args, kwargs, started, outcome)
                return result
            finally:
                _ACTIVE.reset(token)

        return sync_wrapper

    return decorate


def _session_of(obj: Any):
    """适配器直接持有 _session;DeviceOps 经 _adapter 回到适配器。"""
    session = getattr(obj, "_session", None)
    if session is not None:
        return session
    adapter = getattr(obj, "_adapter", None)
    return getattr(adapter, "_session", None)


def _record(
    journal: Journal,
    session: Any,
    op: str,
    args: tuple,
    kwargs: dict,
    started: float,
    outcome: str,
    *,
    error_type: str | None = None,
) -> None:
    """组装并写入;观测失败不得影响调用结果。"""
    try:
        target, extra = _payload(op, args, kwargs)
        if op in _INJECTION_OPS:
            channel = getattr(session, "last_injection_channel", None)
            if channel:
                extra = dict(extra or {})
                extra["channel"] = channel
        journal.record(
            op,
            target=target,
            duration_ms=(time.monotonic() - started) * 1000,
            outcome=outcome,
            error_type=error_type,
            extra=extra,
        )
    except Exception:  # noqa: BLE001 - 观测层绝不改变返回值与异常类型
        pass


def _payload(
    op: str, args: tuple, kwargs: dict
) -> tuple[dict | None, dict | None]:
    """按操作提取目标与安全附加字段;不记录输入原文与读取返回值。"""
    if op == "goto":
        value = args[0] if args else kwargs.get("target", "")
        scheme = value.split(":", 1)[0].lower() if value else "cold_start"
        return None, {"scheme": scheme}
    if op == "input_text":
        text = args[1] if len(args) > 1 else kwargs.get("text", "")
        length = len(text) if isinstance(text, str) else 0
        return _target_dict(_target_arg(args, kwargs)), {"text_length": length}
    if op == "press_key":
        name = args[0] if args else kwargs.get("name")
        return None, {"key": name}
    if op == "scroll":
        direction = args[0] if args else kwargs.get("direction")
        amount = args[1] if len(args) > 1 else kwargs.get("amount")
        return None, {"direction": direction, "amount": amount}
    if op in ("device.tap", "device.long_press"):
        x = args[0] if args else kwargs.get("x")
        y = args[1] if len(args) > 1 else kwargs.get("y")
        return None, {"point": [x, y], "normalized": False}
    if op == "device.swipe":
        values = list(args[:4]) if len(args) >= 4 else [
            kwargs.get("x0"),
            kwargs.get("y0"),
            kwargs.get("x1"),
            kwargs.get("y1"),
        ]
        return None, {
            "from": values[:2],
            "to": values[2:4],
            "normalized": False,
        }
    return _target_dict(_target_arg(args, kwargs)), None


def _target_arg(args: tuple, kwargs: dict) -> Any:
    for value in args:
        if isinstance(value, Target):
            return value
    return kwargs.get("target")


def _target_dict(target: Any) -> dict | None:
    if not isinstance(target, Target):
        return None
    data: dict[str, Any] = {"by": target.by.value, "value": target.value}
    for key in ("index", "confidence"):
        value = getattr(target, key)
        if value is not None:
            data[key] = value
    if target.region is not None:
        data["region"] = list(target.region)
    return data


__all__ = ["Journal", "journaled", "JOURNAL_FILE_NAME", "MAX_BYTES"]
