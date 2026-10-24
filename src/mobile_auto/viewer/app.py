"""网页服务:实时查看、快照与来源/资源限制(plan 8.1–8.3)。

仅监听回环;Host/Origin 校验拒绝跨源;WS 最多 2 客户端、每客户端
仅保留最新帧(慢客户端丢帧不堆积);pytest 持有租约时不主动采集、
状态如实显示;错误统一 code/message/request_id 信封。
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..contracts import Locator, Target
from ..errors import (
    AmbiguousTarget,
    ConfigurationError,
    DeviceBusy,
    DeviceUnavailable,
    InvalidTarget,
    InvalidTemplate,
    MatchOverflow,
    MobileAutoError,
    SessionFaulted,
    StaleHandle,
    StreamUnavailable,
    UnsupportedOperation,
)
from ..scrcpy_channel import ScrcpyChannel
from ..selectors import (
    select_matches,
    validate_locatable,
    visible_only,
)
from ..session import DeviceSession, SessionState
from ..ui_tree import flatten, parse_ui_hierarchy
from .candidates import candidates_for_node, xpath_candidate
from .store import Snapshot, SnapshotStore

_PREVIEW_INTERVAL_SECONDS = 1.0  # u2 截图回退档帧间隔
_STREAM_INTERVAL_SECONDS = 0.1  # 视频流档帧间隔(目标 ~10fps)
_MAX_WS_CLIENTS = 2
_CHANNEL_IDLE_STOP_SECONDS = 30.0  # 无客户端后 server 自停
_CHANNEL_RETRY_BACKOFF_INIT = 2.0
_CHANNEL_RETRY_BACKOFF_MAX = 30.0
_LEASE_PROBE_MIN_INTERVAL = 1.0  # 租约探测节流:降低与 pytest 抢锁的碰撞面

_CAPTURE_TIMEOUT_SECONDS = 10.0
_TREE_INTERVAL_LIMIT_SECONDS = 1.0  # 截图与树采集间隔超限判定(快照不一致)

_STATIC_DIR = Path(__file__).parent / "static"

# 验证查询支持的定位方式:五种原生 + IMAGE(Locator 不含 CSS)
NATIVE_QUERY_LOCATORS = frozenset(Locator)

_ERROR_STATUS = {
    UnsupportedOperation: 400,
    MatchOverflow: 400,
    InvalidTarget: 422,
    InvalidTemplate: 422,
    ConfigurationError: 400,
    StaleHandle: 409,
    DeviceBusy: 409,
    StreamUnavailable: 503,
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


class _ChannelManager:
    """viewer 进程内的 scrcpy 通道:懒启动、退避重试、空闲自停(工单 02)。

    server 启动是设备写操作,经会话租约执行;租约被他人持有或启动失败
    都不致命——预览回退 u2 截图轮询,并按退避自动重试恢复视频流。
    """

    def __init__(
        self,
        session: DeviceSession,
        *,
        idle_stop: float = _CHANNEL_IDLE_STOP_SECONDS,
        retry_init: float = _CHANNEL_RETRY_BACKOFF_INIT,
        retry_max: float = _CHANNEL_RETRY_BACKOFF_MAX,
        channel_factory=None,
    ) -> None:
        self._session = session
        self._idle_stop = idle_stop
        self._retry_init = retry_init
        self._retry_max = retry_max
        self._channel_factory = channel_factory  # None → 真实 ScrcpyChannel
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._channel: ScrcpyChannel | None = None
        self._clients = 0
        self._idle_timer: threading.Timer | None = None
        self._next_retry = 0.0
        self._backoff = retry_init
        self._last_error: str | None = None
        self._lease_probe_at = 0.0
        self._lease_probe_ok = False

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def client_connected(self) -> None:
        with self._lock:
            self._clients += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

    def client_disconnected(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)
            if self._clients == 0 and self._idle_timer is None:
                self._idle_timer = threading.Timer(self._idle_stop, self._stop_idle)
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _stop_idle(self) -> None:
        with self._lock:
            if self._clients == 0 and self._channel is not None:
                self._channel.stop()
                self._channel = None
                self._backoff = self._retry_init

    def invalidate(self, reason: str) -> None:
        """通道损坏(空帧/连接断):回收并安排退避重试;本轮回退 u2。"""
        with self._lock:
            if self._channel is not None:
                self._channel.stop()
                self._channel = None
            self._last_error = reason
            self._next_retry = time.monotonic() + self._backoff
            self._backoff = min(self._backoff * 2, self._retry_max)

    def lease_available(self) -> bool:
        """租约是否空闲(节流探测,≤1Hz);探测即短暂 acquire→release。"""
        with self._lock:
            now = time.monotonic()
            if now - self._lease_probe_at < _LEASE_PROBE_MIN_INTERVAL:
                return self._lease_probe_ok
            self._lease_probe_at = now
        state = self._session.state
        if state not in (SessionState.READY, SessionState.LEASED):
            with self._lock:
                self._lease_probe_ok = False
            return False
        try:
            self._session.acquire_lease()
        except MobileAutoError:
            with self._lock:
                self._lease_probe_ok = False
            return False
        self._session.release_lease()
        with self._lock:
            self._lease_probe_ok = True
        return True

    def channel(self) -> ScrcpyChannel | None:
        """可用通道;未启动/待重试则按退避懒启动(经租约)。"""
        with self._lock:
            if self._channel is not None:
                return self._channel
            if time.monotonic() < self._next_retry:
                return None
        if not self._start_lock.acquire(blocking=False):
            return None  # 另一客户端正在启动,本轮先回退
        try:
            channel = self._start_via_lease()
        finally:
            self._start_lock.release()
        return channel

    def _start_via_lease(self) -> ScrcpyChannel | None:
        acquired = False
        try:
            state = self._session.state
            if state == SessionState.READY:
                self._session.acquire_lease()
                acquired = True
            elif state != SessionState.LEASED:
                raise DeviceUnavailable(f"会话状态 {state.value} 不允许启动视频流")
            # 本进程已持租约(如控制注入短事务内):直接复用,不重复获取
            try:
                factory = self._channel_factory or (
                    lambda serial: ScrcpyChannel(serial)
                )
                channel = factory(self._session.serial)
                channel.start()
            finally:
                if acquired:
                    self._session.release_lease()
        except Exception as exc:
            with self._lock:
                self._last_error = f"视频流启动失败:{exc}"
                self._next_retry = time.monotonic() + self._backoff
                self._backoff = min(self._backoff * 2, self._retry_max)
            return None
        with self._lock:
            self._channel = channel
            self._last_error = None
            self._backoff = self._retry_init
        return channel

    def shutdown(self) -> None:
        """进程内显式回收(测试用);运行中依赖空闲计时与 shell 流 EOF 兜底。"""
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._channel is not None:
                self._channel.stop()
                self._channel = None


def _build_stream_frame(
    manager: _ChannelManager, session: DeviceSession, store: SnapshotStore
) -> dict:
    """预览帧构建:视频流优先,租约被占/通道不可用回退 u2 截图(工单 02)。

    会话故障或租约被他人持有时发状态帧,不显示旧画面冒充实时。
    """
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    if session.state not in (SessionState.READY, SessionState.LEASED):
        busy = session.state == SessionState.LEASED
        return {
            "kind": "status",
            "captured_at": now,
            "device_state": session.state.value,
            "busy": busy,
            "message": "设备忙,暂停采集" if busy else "设备未就绪",
        }
    if not manager.lease_available():
        return {
            "kind": "status",
            "captured_at": now,
            "device_state": session.state.value,
            "busy": True,
            "message": "设备被其他进程占用(如测试运行中),画面已暂停",
        }
    channel = manager.channel()
    if channel is not None:
        if not channel.healthy():
            manager.invalidate("scrcpy 通道不健康(连接断开或泵线程停止)")
        else:
            started = time.monotonic()
            try:
                jpeg, width, height = channel.latest_frame()
            except StreamUnavailable as exc:
                manager.invalidate(str(exc))
            else:
                return {
                    "kind": "frame",
                    "source": "scrcpy",
                    "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                    "width": width,
                    "height": height,
                    "decode_ms": round((time.monotonic() - started) * 1000, 1),
                    "jpeg_base64": base64.b64encode(jpeg).decode("ascii"),
                }
    frame = _build_preview_frame(session, store)
    if frame.get("kind") == "frame":
        frame["source"] = "u2-fallback"
        frame["fallback_reason"] = manager.last_error
    return frame


def create_app(
    session: DeviceSession,
    store: SnapshotStore | None = None,
    *,
    preview_interval: float = _PREVIEW_INTERVAL_SECONDS,
    stream_interval: float = _STREAM_INTERVAL_SECONDS,
    channel_manager: _ChannelManager | None = None,
) -> FastAPI:
    """构建网页应用;会话由调用方注入(lifespan 不拥有启动/关闭)。"""
    store = store or SnapshotStore()
    app = FastAPI(title="mobile-auto viewer", lifespan=None)
    app.state.ws_clients = 0
    app.state.store = store
    app.state.session = session
    app.state.preview_interval = preview_interval
    app.state.stream_interval = stream_interval
    app.state.channel_manager = channel_manager or _ChannelManager(session)
    manager = app.state.channel_manager
    # 直接控制(P1 工单 04):显式启用,默认关闭
    app.state.control_enabled = False
    app.state.last_inject = 0.0

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

    # ── 图像模板制作闭环(plan 8.2;工单 11)─────────────────

    @app.post("/api/v1/templates")
    async def create_template(request: Request):
        """从服务端保留的快照裁剪原始像素生成模板;不接受浏览器图片。"""
        body = await request.json()
        snapshot = store.get(body.get("snapshot_id", ""))
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        region = body.get("region")
        if (
            not isinstance(region, list)
            or len(region) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
        ):
            return error_envelope(request, 422, "invalid-input", "region 必须是四个整数")
        logical = str(body.get("logical") or "").strip()
        if not logical:
            return error_envelope(request, 422, "invalid-input", "模板名不能为空")
        try:
            loaded = await asyncio.to_thread(
                _crop_and_register, session, snapshot, logical, region, body, None
            )
        except MobileAutoError as exc:
            status = 409 if isinstance(exc, StaleHandle) else _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return _template_payload(loaded)

    @app.put("/api/v1/templates/{logical:path}")
    async def update_template(logical: str, request: Request):
        """确认更新:期望旧摘要与新裁剪参数;摘要不符返回冲突(409)。"""
        body = await request.json()
        snapshot = store.get(body.get("snapshot_id", ""))
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        expected = str(body.get("expected_old_sha256") or "")
        try:
            loaded = await asyncio.to_thread(
                _crop_and_register, session, snapshot, logical, body.get("region"), body, expected
            )
        except MobileAutoError as exc:
            status = 409 if isinstance(exc, StaleHandle) else _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return _template_payload(loaded)

    @app.get("/api/v1/templates")
    async def list_templates(request: Request):
        from ..assets import TemplateStore

        entries = [
            {
                "logical": logical,
                "version_id": entry["version_id"],
                "image_sha256": entry["image_sha256"],
            }
            for logical, entry in TemplateStore(
                session.config.assets_dir
            ).list_templates().items()
        ]
        return {"templates": entries}

    @app.get("/api/v1/templates/{logical:path}/image")
    async def template_image(logical: str, request: Request):
        from ..assets import TemplateStore

        try:
            loaded = TemplateStore(session.config.assets_dir).load(logical)
        except MobileAutoError as exc:
            return error_envelope(request, 404, "not-found", str(exc))
        return Response(
            content=loaded.png,
            media_type="image/png",
            headers={"cache-control": "no-store"},
        )

    @app.post("/api/v1/templates/validate")
    async def validate_template(request: Request):
        """IMAGE 定位验证:重新采集匹配,回传数量、分数与边界框。"""
        body = await request.json()
        target_body = body.get("target") or {}
        try:
            region = target_body.get("region")
            target = Target(
                by=target_body.get("by") or "image",
                value=target_body.get("value") or "",
                index=target_body.get("index"),
                confidence=target_body.get("confidence"),
                region=tuple(region) if region else None,
            )
            if target.by.value != "image":
                return error_envelope(
                    request, 422, "invalid-input", "该端点仅接受 IMAGE 目标"
                )
        except MobileAutoError as exc:
            return error_envelope(request, 422, "invalid-target", str(exc))
        try:
            snapshot, matches, scores = await asyncio.to_thread(
                capture_and_query_sync, session, store, target, True
            )
        except AmbiguousTarget as exc:
            return error_envelope(request, 409, "ambiguous-target", str(exc))
        return {
            "snapshot_id": snapshot.id,
            "captured_at": snapshot.captured_at,
            "count": len(matches),
            "matches": matches,
            "scores": scores,
            "passed": len(matches) == 1,
            "note": (
                "唯一匹配,验证通过"
                if len(matches) == 1
                else ("零匹配:目标未出现" if not matches else f"多匹配 {len(matches)} 个:需序号")
            ),
        }

    # ── 原生拾取与定位验证(plan 8.2)─────────────────────────

    @app.post("/api/v1/pick")
    async def pick(request: Request):
        body = await request.json()
        snapshot = store.get(body.get("snapshot_id", ""))
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        if snapshot.tree_root is None:
            return error_envelope(
                request, 400, "capability-unavailable",
                snapshot.tree_error or "UI 树不可用,原生拾取已禁用;图像定位不受影响",
            )
        x, y = int(body.get("x", -1)), int(body.get("y", -1))
        if not (0 <= x < snapshot.width and 0 <= y < snapshot.height):
            return error_envelope(request, 422, "invalid-input", "拾取坐标超出快照范围")
        hits = [
            node
            for node in flatten(snapshot.tree_root)
            if node.bounds
            and node.bounds[0] <= x < node.bounds[2]
            and node.bounds[1] <= y < node.bounds[3]
        ]
        hits.sort(key=lambda n: (n.bounds[2] - n.bounds[0]) * (n.bounds[3] - n.bounds[1]))
        candidates = []
        for node in hits[:8]:
            locators = candidates_for_node(node)
            xpath = xpath_candidate(snapshot.tree_root, node)
            if xpath:
                locators.append(xpath)
            candidates.append(
                {
                    "class_name": node.class_name,
                    "bounds": list(node.bounds),
                    "text": node.text,
                    "resource_id": node.resource_id,
                    "locators": locators,
                }
            )
        return {
            "snapshot_id": snapshot.id,
            "point": [x, y],
            "candidates": candidates,
            "overlap": len(candidates) > 1,  # 重叠候选由用户选择,不静默选节点
        }

    @app.post("/api/v1/locators/validate")
    async def validate_locator(request: Request):
        body = await request.json()
        target_body = body.get("target") or {}
        try:
            region = target_body.get("region")
            target = Target(
                by=target_body.get("by"),
                value=target_body.get("value"),
                index=target_body.get("index"),
                confidence=target_body.get("confidence"),
                region=tuple(region) if region else None,
            )
        except MobileAutoError as exc:
            return error_envelope(request, 422, "invalid-target", str(exc))
        try:
            snapshot, matches = await asyncio.to_thread(
                capture_and_query_sync, session, store, target
            )
        except AmbiguousTarget as exc:
            return error_envelope(request, 409, "ambiguous-target", str(exc))
        return {
            "snapshot_id": snapshot.id,
            "captured_at": snapshot.captured_at,
            "count": len(matches),
            "matches": matches,
            "passed": len(matches) == 1,
            "note": (
                "唯一匹配,验证通过"
                if len(matches) == 1
                else (
                    "零匹配:目标未出现,验证不通过"
                    if not matches
                    else f"多匹配 {len(matches)} 个:需序号或收窄定位,不视为通过"
                )
            ),
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
        manager: _ChannelManager = app.state.channel_manager
        manager.client_connected()
        try:
            await ws.accept()
            sender = _PreviewSender(ws)
            app.state.preview_sender = sender
            try:
                while True:
                    frame = await asyncio.to_thread(
                        _build_stream_frame, manager, session, store
                    )
                    await sender.send_latest(frame)
                    # 视频流档 ~10fps;回退/状态档退回 u2 截图节奏
                    interval = (
                        app.state.stream_interval
                        if frame.get("source") == "scrcpy"
                        else app.state.preview_interval
                    )
                    await asyncio.sleep(interval)
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                app.state.preview_sender = None
        finally:
            app.state.ws_clients -= 1
            manager.client_disconnected()

    # ── 直接控制(P1 工单 04;显式启用)────────────────────────

    _INJECT_MIN_INTERVAL_SECONDS = 0.05  # 注入节流

    @app.post("/api/v1/control")
    async def set_control_mode(request: Request):
        body = await request.json()
        enabled = bool(body.get("enabled"))
        app.state.control_enabled = enabled
        return {
            "enabled": enabled,
            "request_id": request.state.request_id,
            "note": "直接控制已启用" if enabled else "直接控制已关闭",
        }

    def _gate_control(request: Request) -> JSONResponse | None:
        """控制模式与节流门控;通过返回 None。"""
        if not app.state.control_enabled:
            return error_envelope(request, 400, "control-disabled", "直接控制未启用")
        now = time.monotonic()
        if now - app.state.last_inject < _INJECT_MIN_INTERVAL_SECONDS:
            return error_envelope(request, 429, "throttled", "注入过于频繁(≥50ms 间隔)")
        app.state.last_inject = now
        return None

    def _normalized(body: dict, names: tuple[str, ...]) -> list[float] | None:
        values = []
        for name in names:
            value = body.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            values.append(min(max(float(value), 0.0), 1.0))
        return values

    def _inject(operation):
        """短事务注入:租约(409 门控)→ 队列串行执行 → 释放。

        视频通道经管理器获取(懒启动遵循同一租约门控);注入不成功不
        释放租约的窗口极小且不回滚,失败时如实报错。
        """
        session.acquire_lease()  # LEASED(pytest 持有)→ DeviceBusy → 409
        try:
            channel = manager.channel()
            if channel is None or not channel.healthy():
                raise StreamUnavailable("视频流通道不可用,无法注入")
            future = session.submit(lambda backend: operation(channel))
            try:
                return future.result(timeout=10.0)
            except TimeoutError as exc:
                future.cancel()
                raise DeviceUnavailable("注入执行超时") from exc
        finally:
            session.release_lease()

    @app.post("/api/v1/input/touch")
    async def input_touch(request: Request):
        body = await request.json()
        gate = _gate_control(request)
        if gate is not None:
            return gate
        values = _normalized(body, ("nx", "ny"))
        if values is None:
            return error_envelope(request, 422, "invalid-input", "nx/ny 必须是数值")
        nx, ny = values
        try:
            await asyncio.to_thread(
                _inject,
                lambda channel: channel.tap(
                    *(
                        lambda size: (
                            int(nx * size[0]),
                            int(ny * size[1]),
                        )
                    )(channel.screen_size())
                ),
            )
        except MobileAutoError as exc:
            status = _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return {"ok": True, "request_id": request.state.request_id}

    @app.post("/api/v1/input/swipe")
    async def input_swipe(request: Request):
        body = await request.json()
        gate = _gate_control(request)
        if gate is not None:
            return gate
        values = _normalized(body, ("nx0", "ny0", "nx1", "ny1"))
        if values is None:
            return error_envelope(
                request, 422, "invalid-input", "nx0/ny0/nx1/ny1 必须是数值"
            )
        nx0, ny0, nx1, ny1 = values
        duration_ms = body.get("duration_ms", 300)
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
            return error_envelope(request, 422, "invalid-input", "duration_ms 必须是数值")
        duration_s = min(max(float(duration_ms) / 1000, 0.05), 5.0)
        try:
            await asyncio.to_thread(
                _inject,
                lambda channel: channel.swipe(
                    *(
                        lambda size: (
                            int(nx0 * size[0]),
                            int(ny0 * size[1]),
                            int(nx1 * size[0]),
                            int(ny1 * size[1]),
                        )
                    )(channel.screen_size()),
                    duration_s=duration_s,
                ),
            )
        except MobileAutoError as exc:
            status = _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return {"ok": True, "request_id": request.state.request_id}

    @app.post("/api/v1/input/key")
    async def input_key(request: Request):
        body = await request.json()
        gate = _gate_control(request)
        if gate is not None:
            return gate
        name = str(body.get("name") or "")
        try:
            await asyncio.to_thread(_inject, lambda channel: channel.press_key(name))
        except MobileAutoError as exc:
            status = _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return {"ok": True, "request_id": request.state.request_id}

    @app.post("/api/v1/input/wake")
    async def input_wake(request: Request):
        gate = _gate_control(request)
        if gate is not None:
            return gate
        try:
            await asyncio.to_thread(_inject, lambda channel: channel.wake())
        except MobileAutoError as exc:
            status = _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return {"ok": True, "request_id": request.state.request_id}

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
        # 预览帧是瞬态观察,不入快照库:不挤占拾取/验证用的用户快照
        session.acquire_lease()
        try:
            future = session.submit(lambda backend: backend.screenshot_png())
            png = future.result(timeout=_CAPTURE_TIMEOUT_SECONDS)
        finally:
            session.release_lease()
        return {
            "kind": "frame",
            "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "width": 0,
            "height": 0,
            "jpeg_base64": base64.b64encode(_png_to_jpeg(png)).decode("ascii"),
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
        tree_root=parse_ui_hierarchy(tree_xml) if tree_xml and len(tree_xml) >= 100 else None,
    )


def capture_and_query_sync(
    session: DeviceSession, store: SnapshotStore, target: Target, with_scores: bool = False
):
    """验证短事务:获取租约 → 新采集截图 + 按定位方式重查询 → 释放。

    返回 (新快照, 命中列表[, 分数列表]);查询语义与驱动一致。
    """

    session.acquire_lease()
    try:
        future = session.submit(lambda backend: _query_payload(backend, session, store, target))
        try:
            payload = future.result(timeout=_CAPTURE_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            future.cancel()
            raise DeviceUnavailable("定位验证查询超时") from exc
    finally:
        session.release_lease()
    if with_scores:
        png, screen, matches, scores = payload
    else:
        png, screen, matches = payload
        scores = None
    now = datetime.now(UTC)
    snapshot = Snapshot(
        png=png,
        width=int(screen[0]),
        height=int(screen[1]),
        orientation="portrait" if screen[1] >= screen[0] else "landscape",
        captured_at=now.isoformat(timespec="milliseconds"),
    )
    store.add(snapshot)
    return (snapshot, matches, scores) if with_scores else (snapshot, matches)


def _query_payload(backend, session, store, target: Target):
    from ..assets import TemplateStore
    from ..image import match_template

    png = backend.screenshot_png()
    screen = backend.screen_size()
    scores = None
    if target.by.value == "image":
        template = TemplateStore(session.config.assets_dir).load(target.value)
        results = match_template(
            png, template.png, confidence=target.confidence, region=target.region
        )
        chosen = select_matches(results, target)
        scores = [round(r.confidence, 6) for r in chosen]
    else:
        validate_locatable(target, NATIVE_QUERY_LOCATORS)
        elements = backend.find_elements(target.by, target.value)
        chosen = select_matches(visible_only(elements, screen), target)
    matches = []
    for item in chosen:
        if hasattr(item, "bounds"):  # 后端元素:bounds 为 (left, top, right, bottom)
            left, top, right, bottom = item.bounds
        else:  # MatchResult:已是 (x, y, width, height)
            left, top, right, bottom = item.x, item.y, item.x + item.width, item.y + item.height
        matches.append(
            {
                "x": int(left),
                "y": int(top),
                "width": int(right - left),
                "height": int(bottom - top),
            }
        )
    if scores is None:
        return png, screen, matches
    return png, screen, matches, scores


def _crop_and_register(session, snapshot, logical, region, body, expected_old):
    """服务端从保留快照裁剪原始像素,经资产库 create/update 登记。"""
    import numpy as np

    from ..assets import TemplateStore
    from ..image import decode_rgb

    if (
        not isinstance(region, list)
        or len(region) != 4
        or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
    ):
        raise InvalidTarget("region 必须是四个整数 [x, y, width, height]")
    rx, ry, rw, rh = region
    frame = decode_rgb(snapshot.png, what="快照")
    h, w = frame.shape[:2]
    if rw <= 0 or rh <= 0 or rx < 0 or ry < 0 or rx + rw > w or ry + rh > h:
        raise InvalidTemplate(f"裁剪区域 {region} 超出快照 {w}x{h};请收缩框选")
    crop = np.ascontiguousarray(frame[ry : ry + rh, rx : rx + rw])
    ok, buf = _cv2_imencode(crop)
    if not ok:
        raise InvalidTemplate("模板编码失败")
    store = TemplateStore(session.config.assets_dir)
    if expected_old is None:
        return store.create(
            logical,
            buf.tobytes(),
            source_width=snapshot.width,
            source_height=snapshot.height,
            orientation=snapshot.orientation,
            app_package=session.config.app_package or "",
            note=str(body.get("note") or ""),
        )
    return store.update(
        logical,
        buf.tobytes(),
        expected_old,
        source_width=snapshot.width,
        source_height=snapshot.height,
        orientation=snapshot.orientation,
        app_package=session.config.app_package or "",
        note=str(body.get("note") or ""),
    )


def _cv2_imencode(arr_rgb):
    import cv2

    return cv2.imencode(".png", arr_rgb[:, :, ::-1])


def _template_payload(loaded):
    return {
        "logical": loaded.logical,
        "version_id": loaded.version_id,
        "sha256": loaded.sha256,
        "width": loaded.width,
        "height": loaded.height,
    }


def _capture_payload(backend, with_tree: bool):
    png = backend.screenshot_png()
    tree_xml = backend.dump_ui() if with_tree else None
    width, height = backend.screen_size()
    return png, int(width), int(height), tree_xml, None


def _normalized_tree_json(tree_xml: str) -> str | None:
    if not tree_xml or len(tree_xml) < 100:
        return None  # 空 dump(部分 ROM 偶发)按未采集处理,错误信息由调用方标注
    import json

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
