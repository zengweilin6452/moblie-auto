"""网页服务:实时查看、快照与来源/资源限制(plan 8.1–8.3)。

仅监听回环;Host/Origin 校验拒绝跨源;WS 最多 2 客户端、每客户端
仅保留最新帧(慢客户端丢帧不堆积);pytest 持有租约时不主动采集、
状态如实显示;错误统一 code/message/request_id 信封。
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..errors import (
    ConfigurationError,
    DeviceBusy,
    DeviceUnavailable,
    InvalidTarget,
    InvalidTemplate,
    MatchOverflow,
    MobileAutoError,
    SessionFaulted,
    StaleHandle,
    UnsupportedOperation,
)
from ..session import DeviceSession, SessionState
from .store import Snapshot, SnapshotStore

_PREVIEW_INTERVAL_SECONDS = 1.0
_MAX_WS_CLIENTS = 2
_CAPTURE_TIMEOUT_SECONDS = 10.0
_TREE_INTERVAL_LIMIT_SECONDS = 1.0  # 截图与树采集间隔超限判定(快照不一致)

_STATIC_DIR = Path(__file__).parent / "static"

_ERROR_STATUS = {
    UnsupportedOperation: 400,
    MatchOverflow: 400,
    InvalidTarget: 400,
    InvalidTemplate: 400,
    ConfigurationError: 400,
    StaleHandle: 409,
    DeviceBusy: 409,
    SessionFaulted: 503,
    DeviceUnavailable: 503,
}


def error_envelope(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


def create_app(
    session: DeviceSession,
    store: SnapshotStore | None = None,
    *,
    preview_interval: float = _PREVIEW_INTERVAL_SECONDS,
) -> FastAPI:
    """构建网页应用;会话由调用方注入(lifespan 不拥有启动/关闭)。"""
    store = store or SnapshotStore()
    app = FastAPI(title="mobile-auto viewer", lifespan=None)
    app.state.ws_clients = 0
    app.state.store = store
    app.state.session = session
    app.state.preview_interval = preview_interval

    @app.middleware("http")
    async def request_id_and_origin(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if not _host_allowed(host):
            return error_envelope(request, 403, "forbidden-origin", f"Host 不受允许:{host}")
        if origin is not None and not _origin_allowed(origin):
            return error_envelope(request, 403, "forbidden-origin", f"来源不受允许:{origin}")
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(MobileAutoError)
    async def domain_error_handler(request: Request, exc: MobileAutoError):
        status = _ERROR_STATUS.get(type(exc), 400)
        return error_envelope(request, status, type(exc).__name__, str(exc))

    # ── 状态 ──────────────────────────────────────────────────

    @app.get("/api/v1/status")
    async def status(request: Request):
        session: DeviceSession = app.state.session
        return {
            "service_version": __version__,
            "request_id": request.state.request_id,
            "device": {
                "serial": session.serial,
                "state": session.state.value,
                "lease_held": session.state == SessionState.LEASED,
                "busy": session.state == SessionState.LEASED,
            },
            "capture": {
                "can_capture": session.state == SessionState.READY,
                "snapshots": len(store.snapshot_ids()),
            },
        }

    # ── 快照 ──────────────────────────────────────────────────

    @app.post("/api/v1/snapshots")
    async def create_snapshot(request: Request):
        body = await request.json() if await request.body() else {}
        with_tree = bool(body.get("with_tree", False))
        snapshot = await asyncio.to_thread(capture_snapshot_sync, session, with_tree)
        store.add(snapshot)
        return {
            "snapshot_id": snapshot.id,
            "width": snapshot.width,
            "height": snapshot.height,
            "orientation": snapshot.orientation,
            "captured_at": snapshot.captured_at,
            "tree_captured_at": snapshot.tree_captured_at,
            "tree_error": snapshot.tree_error,
            "consistent": snapshot.tree_error is None
            or _interval_ok(snapshot.captured_at, snapshot.tree_captured_at),
        }

    @app.get("/api/v1/snapshots/{snapshot_id}/image")
    async def snapshot_image(snapshot_id: str, request: Request):
        snapshot = store.get(snapshot_id)
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        return Response(
            content=snapshot.png,
            media_type="image/png",
            headers={"cache-control": "no-store"},
        )

    @app.get("/api/v1/snapshots/{snapshot_id}/tree")
    async def snapshot_tree(snapshot_id: str, request: Request):
        snapshot = store.get(snapshot_id)
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        if snapshot.tree_json is None:
            return error_envelope(
                request,
                400,
                "capability-unavailable",
                snapshot.tree_error or "该快照未采集 UI 树",
            )
        return Response(
            content=snapshot.tree_json,
            media_type="application/json",
            headers={"cache-control": "no-store"},
        )

    @app.post("/api/v1/snapshots/{snapshot_id}/freeze")
    async def freeze_snapshot(snapshot_id: str, request: Request):
        body = await request.json()
        frozen = bool(body.get("frozen", True))
        snapshot = store.freeze(snapshot_id, frozen)
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        return {
            "snapshot_id": snapshot.id,
            "frozen": snapshot.frozen,
            "frozen_at": snapshot.frozen_at.isoformat() if snapshot.frozen_at else None,
        }

    # ── 预览 WS ───────────────────────────────────────────────

    @app.websocket("/api/v1/preview")
    async def preview(ws: WebSocket):
        origin = ws.headers.get("origin")
        if origin is not None and not _origin_allowed(origin):
            await ws.close(code=4403)
            return
        if app.state.ws_clients >= _MAX_WS_CLIENTS:
            await ws.close(code=4429)
            return
        app.state.ws_clients += 1
        try:
            await ws.accept()
            sender = _PreviewSender(ws)
            app.state.preview_sender = sender
            try:
                while True:
                    await sender.send_latest(
                        _build_preview_frame(session, store)
                    )
                    await asyncio.sleep(app.state.preview_interval)
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                app.state.preview_sender = None
        finally:
            app.state.ws_clients -= 1

    app.state.static_dir = _STATIC_DIR
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


class _PreviewSender:
    """每客户端仅保留最新帧:上一帧未发完则丢弃新帧,不堆积。"""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._busy = False
        self._dropped = 0

    async def send_latest(self, frame: dict) -> None:
        if self._busy:
            self._dropped += 1  # 慢客户端:丢帧不排队
            return
        self._busy = True
        try:
            await self._ws.send_json(frame)
        finally:
            self._busy = False


def _build_preview_frame(session: DeviceSession, store: SnapshotStore) -> dict:
    """预览帧:仅观察;pytest 持有租约(LEASED)时不主动采集。"""
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    if session.state != SessionState.READY:
        busy = session.state == SessionState.LEASED
        return {
            "kind": "status",
            "captured_at": now,
            "device_state": session.state.value,
            "busy": busy,
            "message": (
                "设备忙(其他进程持租约),暂停采集" if busy else "设备未就绪"
            ),
        }
    try:
        snapshot = capture_snapshot_sync(session, with_tree=False)
        store.add(snapshot)
        return {
            "kind": "frame",
            "captured_at": snapshot.captured_at,
            "snapshot_id": snapshot.id,
            "width": snapshot.width,
            "height": snapshot.height,
            "jpeg_base64": base64.b64encode(_png_to_jpeg(snapshot.png)).decode("ascii"),
        }
    except MobileAutoError as exc:
        return {
            "kind": "error",
            "captured_at": now,
            "code": type(exc).__name__,
            "message": str(exc),
        }


def _png_to_jpeg(png: bytes) -> bytes:
    import cv2
    import numpy as np

    decoded = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    ok, buf = cv2.imencode(".jpg", decoded, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else png


def capture_snapshot_sync(session: DeviceSession, with_tree: bool) -> Snapshot:
    """短事务采集:获取租约 → 截图(可选树)→ 释放;有界等待。"""
    session.acquire_lease()  # LEASED → DeviceBusy(409)
    try:
        if session.state != SessionState.LEASED:
            raise DeviceUnavailable("会话未就绪,无法采集")
        future = session.submit(lambda backend: _capture_payload(backend, with_tree))
        try:
            payload = future.result(timeout=_CAPTURE_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            future.cancel()
            raise DeviceUnavailable("采集超时") from exc
    finally:
        session.release_lease()
    png, width, height, tree_xml, tree_error = payload
    now = datetime.now(UTC)
    return Snapshot(
        png=png,
        width=width,
        height=height,
        orientation="portrait" if height >= width else "landscape",
        captured_at=now.isoformat(timespec="milliseconds"),
        tree_json=tree_xml and _normalized_tree_json(tree_xml),
        tree_captured_at=now.isoformat(timespec="milliseconds") if tree_xml else None,
        tree_error=tree_error,
    )


def _capture_payload(backend, with_tree: bool):
    png = backend.screenshot_png()
    tree_xml = backend.dump_ui() if with_tree else None
    width, height = backend.screen_size()
    return png, int(width), int(height), tree_xml, None


def _normalized_tree_json(tree_xml: str) -> str | None:
    if not tree_xml or len(tree_xml) < 100:
        return None  # 空 dump(部分 ROM 偶发)按未采集处理,错误信息由调用方标注
    import json

    from ..ui_tree import flatten, parse_ui_hierarchy

    root = parse_ui_hierarchy(tree_xml)
    indexed = list(enumerate(flatten(root)))
    by_identity = {id(node): i for i, node in indexed}
    nodes = [
        {
            "index": i,
            "class_name": n.class_name,
            "resource_id": n.resource_id,
            "text": n.text,
            "content_desc": n.content_desc,
            "bounds": list(n.bounds) if n.bounds else None,
            "clickable": n.clickable,
            "children": [by_identity[id(c)] for c in n.children],
        }
        for i, n in indexed
    ]
    return json.dumps({"nodes": nodes}, ensure_ascii=False)


def _interval_ok(captured_at: str, tree_captured_at: str | None) -> bool:
    if not tree_captured_at:
        return False
    a = datetime.fromisoformat(captured_at)
    b = datetime.fromisoformat(tree_captured_at)
    return abs((b - a).total_seconds()) <= _TREE_INTERVAL_LIMIT_SECONDS


def _host_allowed(host: str) -> bool:
    hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return hostname in ("127.0.0.1", "localhost", "[::1]", "testserver")


def _origin_allowed(origin: str) -> bool:
    for prefix in ("http://127.0.0.1", "http://localhost", "http://[::1]"):
        if origin.startswith(prefix):
            return True
    return False
