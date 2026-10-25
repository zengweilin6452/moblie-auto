"""网页服务:多设备查看、实时预览、快照与来源/资源限制。

仅监听回环;Host/Origin 校验拒绝跨源。设备经 ?device= 参数寻址,
会话按 serial 懒创建并各自持有租约与串行队列;WS 每设备最多
4 客户端、每客户端仅保留最新帧(慢客户端丢帧不堆积)。预览读取
(scrcpy 最新帧)不占租约——测试运行中画面不中断;截图回退与写操作
(注入/采集)仍经租约门控;错误统一 code/message/request_id 信封。
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..contracts import Locator, Target
from ..core.backend import enumerate_device_states
from ..core.selectors import (
    select_matches,
    validate_locatable,
    visible_only,
)
from ..core.session import DeviceSession, SessionState
from ..core.ui_tree import flatten, parse_ui_hierarchy
from ..errors import (
    AmbiguousTarget,
    ConfigurationError,
    DeviceBusy,
    DeviceUnavailable,
    DuplicateTemplate,
    InvalidTarget,
    InvalidTemplate,
    MatchOverflow,
    MobileAutoError,
    SessionFaulted,
    StaleHandle,
    StreamUnavailable,
    UnsupportedOperation,
)
from ..stream.channel import ScrcpyChannel
from ..stream.supervisor import (
    IDLE_STOP_SECONDS,
    RETRY_BACKOFF_INIT,
    RETRY_BACKOFF_MAX,
    ServerSupervisor,
)
from .candidates import candidates_for_node, xpath_candidate
from .store import Snapshot, SnapshotStore

_PREVIEW_INTERVAL_SECONDS = 1.0  # u2 截图回退档帧间隔
_STREAM_INTERVAL_SECONDS = 0.1  # 视频流档帧间隔(目标 ~10fps)
_MAX_WS_CLIENTS_PER_DEVICE = 4  # 同一设备的预览连接上限(按设备计数)

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
    DuplicateTemplate: 409,  # 同名创建是冲突;前端以此触发确认更新流程
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


def _empty_device_info() -> dict:
    """空档案:各字段未知(未授权/离线/探测失败)。"""
    return {"brand": None, "model": None, "android_version": None, "resolution": None}


def _probe_device_info(serial: str) -> dict:
    """adb 只读探测设备档案(getprop + wm size);失败各字段为 None。

    读取无设备副作用:不建会话、不触碰会话状态机(P2 工单 06)。
    """
    import adbutils

    def prop(device, name: str) -> str | None:
        try:
            return device.shell(f"getprop {name}").strip() or None
        except Exception:
            return None

    try:
        device = adbutils.adb.device(serial=serial)
        resolution = None
        try:
            for part in device.shell("wm size").replace("\n", " ").split():
                if "x" in part and part[0].isdigit():
                    resolution = part  # Physical size 行,Override 忽略
                    break
        except Exception:
            pass
        return {
            "brand": prop(device, "ro.product.brand"),
            "model": prop(device, "ro.product.model"),
            "android_version": prop(device, "ro.build.version.release"),
            "resolution": resolution,
        }
    except Exception:
        return _empty_device_info()


# ── 失败证据只读浏览(P2 工单 08)─────────────────────────

_EVIDENCE_ID = re.compile(r"[0-9a-f]{16}")
_EVIDENCE_LIST_LIMIT = 200
_EVIDENCE_MEDIA = {
    ".png": "image/png",
    ".xml": "text/xml; charset=utf-8",
    ".json": "application/json",
}


def _list_evidence(config) -> list[dict]:
    """artifacts 下任意层级(xdist worker 子目录)的证据清单,新在前。"""
    import json

    try:
        meta_paths = list(
            Path(config.artifacts_dir).glob("**/evidence/*/meta.json")
        )
    except OSError:
        return []
    items = []
    for meta_path in meta_paths[:_EVIDENCE_LIST_LIMIT]:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append(
            {
                "evidence_id": meta.get("evidence_id"),
                "created_at": meta.get("created_at"),
                "device": (meta.get("device") or {}).get("serial"),
                "failure": (meta.get("failure") or {}).get("type"),
                "frame_source": (meta.get("frame_source") or {}).get("source"),
                "artifacts": [
                    a.get("name") for a in meta.get("artifacts", []) if isinstance(a, dict)
                ],
            }
        )
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items


def _read_evidence_meta(config, evidence_id: str) -> dict | None:
    import json

    if not _EVIDENCE_ID.fullmatch(evidence_id):
        return None
    for path in Path(config.artifacts_dir).glob(
        f"**/evidence/{evidence_id}/meta.json"
    ):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def _read_evidence_file(config, evidence_id: str, name: str):
    """读取证据目录内登记过的文件;文件名必须命中 meta 白名单,天然防穿越。"""
    meta = _read_evidence_meta(config, evidence_id)
    if meta is None:
        return None
    allowed = {a.get("name") for a in meta.get("artifacts", []) if isinstance(a, dict)}
    if name not in allowed:
        return None
    for path in Path(config.artifacts_dir).glob(
        f"**/evidence/{evidence_id}/{name}"
    ):
        try:
            payload = path.read_bytes()
        except OSError:
            return None
        return payload, _EVIDENCE_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return None


def _probe_leases(serials: list[str]) -> list[dict]:
    """各设备当前占用与持有者(只读探测;持锁文件的任何 I/O 都被拒,
    故经伴生文件读信息、试锁判占用)。"""
    from ..core.lease import DeviceLease, read_holder

    items = []
    for serial in serials:
        lease = DeviceLease(serial)
        try:
            held = lease.probe_held()
        except Exception:
            held = False
        items.append(
            {
                "serial": serial,
                "held": held,
                "holder": read_holder(serial) if held else None,
            }
        )
    return items


class _ChannelManager(ServerSupervisor):
    """viewer 进程内的 scrcpy 通道监督器:启动经租约短事务,预览客户端即用户。

    server 启动是设备写操作,经会话租约执行;租约被他人持有或启动失败
    都不致命——预览回退 u2 截图轮询,并按退避自动重试恢复视频流。
    生命周期语义(保活/空闲自停/退避/回收)由 ServerSupervisor 统一提供
    (P2 工单 01),本类只注入 viewer 的租约启动纪律与客户端计数。
    """

    def __init__(
        self,
        session: DeviceSession,
        *,
        idle_stop: float = IDLE_STOP_SECONDS,
        retry_init: float = RETRY_BACKOFF_INIT,
        retry_max: float = RETRY_BACKOFF_MAX,
        channel_factory=None,
    ) -> None:
        self._session = session
        self._channel_factory = channel_factory  # None → 真实 ScrcpyChannel
        super().__init__(
            starter=self._start_via_lease,
            idle_stop=idle_stop,
            retry_init=retry_init,
            retry_max=retry_max,
        )

    @property
    def clients(self) -> int:
        """当前预览客户端数;用于每设备连接上限判定。"""
        return self.user_count

    def client_connected(self) -> None:
        self.acquire_user()

    def client_disconnected(self) -> None:
        self.release_user()

    def _start_via_lease(self) -> ScrcpyChannel:
        """短事务启动:READY 时获取租约、启动后释放;本进程已持(LEASED)
        如控制注入事务内,直接启动不重复获取。"""
        acquired = False
        state = self._session.state
        if state == SessionState.READY:
            self._session.acquire_lease()
            acquired = True
        elif state != SessionState.LEASED:
            raise DeviceUnavailable(f"会话状态 {state.value} 不允许启动视频流")
        try:
            factory = self._channel_factory or (
                lambda serial: ScrcpyChannel(serial)
            )
            channel = factory(self._session.serial)
            channel.start()
            return channel
        finally:
            if acquired:
                self._session.release_lease()


class _DeviceEntry:
    """注册表条目:一台设备的会话与通道管理器;会话可选(未连接)。"""

    __slots__ = ("session", "manager")

    def __init__(self, session: DeviceSession | None, manager: _ChannelManager | None) -> None:
        self.session = session
        self.manager = manager


class _DeviceRegistry:
    """serial → 会话/通道管理器的懒创建注册表。

    设备列表来自 adb 枚举与已建会话的并集;会话在首次被路由寻址时
    创建并连接(失败如实报错且不缓存,下次寻址重试)。默认设备为
    config.device_serial;每台设备独立持有串行队列与租约文件,互不阻塞。
    """

    def __init__(
        self,
        config,
        *,
        enumerator=None,
        session_factory=None,
        manager_factory=None,
    ) -> None:
        self._config = config
        self._enumerator = enumerator or enumerate_device_states
        self._session_factory = session_factory or self._default_session_factory
        self._manager_factory = manager_factory or _ChannelManager
        self._lock = threading.Lock()
        self._entries: dict[str, _DeviceEntry] = {}

    @classmethod
    def single(
        cls, session: DeviceSession, manager: _ChannelManager | None = None
    ) -> _DeviceRegistry:
        """单设备兼容视图:预置已启动会话(测试与旧调用方),不触碰 adb 枚举。"""
        registry = cls.__new__(cls)
        registry._config = session.config
        registry._enumerator = lambda: {session.serial: "device"} if session.serial else {}
        registry._session_factory = None  # 预置会话,不再创建
        registry._manager_factory = _ChannelManager
        registry._lock = threading.Lock()
        registry._entries = {}
        if session.serial:
            registry._entries[session.serial] = _DeviceEntry(session, manager)
        return registry

    def _default_session_factory(self, serial: str) -> DeviceSession:
        session = DeviceSession(dataclasses.replace(self._config, device_serial=serial))
        session.start()  # 连接失败 → DeviceUnavailable,由调用方如实报错
        session.release_lease()  # 网页空闲态 READY
        return session

    def default_serial(self) -> str | None:
        configured = self._config.device_serial
        if configured:
            return configured
        states = self._adb_states()
        authorized = sorted(s for s, v in states.items() if v == "device")
        return authorized[0] if len(authorized) == 1 else None

    @property
    def config(self):
        return self._config

    def _adb_states(self) -> dict[str, str]:
        try:
            return dict(self._enumerator())
        except MobileAutoError:
            return {}

    def devices(self) -> list[dict]:
        """adb 可见设备 ∪ 已建会话;不创建会话、无副作用。"""
        states = self._adb_states()
        with self._lock:
            entries = dict(self._entries)
        default = self.default_serial()
        serials = sorted(set(states) | set(entries))
        result = []
        for serial in serials:
            entry = entries.get(serial)  # adb 可见但尚未建会话:session 为 null
            result.append(
                {
                    "serial": serial,
                    "state": states.get(serial)
                    or ("connected" if entry is not None else "unknown"),
                    "session": entry.session.state.value
                    if entry is not None and entry.session is not None
                    else None,
                    "default": serial == default,
                }
            )
        return result

    def entry(self, serial: str, *, create: bool = False) -> _DeviceEntry | None:
        """已存在的条目;create 时懒建会话(连接设备)后返回。"""
        with self._lock:
            entry = self._entries.get(serial)
        if entry is not None and entry.session is not None:
            return entry
        if not create:
            return entry
        if self._session_factory is None:  # 单设备视图:无工厂即不可新建
            return entry
        session = self._session_factory(serial)  # 失败不缓存:下次寻址重试
        with self._lock:
            entry = self._entries.get(serial)
            if entry is not None and entry.session is not None:
                # 并发建会话:保留先建成的,丢弃本次(由 GC 关闭冗余会话)
                session.close()
                return entry
            manager = entry.manager if entry is not None else None
            entry = _DeviceEntry(session, manager or self._manager_factory(session))
            self._entries[serial] = entry
        return entry

    def resolve(
        self, device_param: str | None
    ) -> tuple[str, DeviceSession, _ChannelManager]:
        """路由寻址:None → 默认设备;未建会话则连接。失败抛 MobileAutoError。"""
        serial = device_param or self.default_serial()
        if not serial:
            raise DeviceUnavailable("未指定设备且无法确定默认设备;请用 ?device= 指定")
        entry = self.entry(serial, create=True)
        if entry is None or entry.session is None:
            raise DeviceUnavailable(f"设备 {serial} 会话不可用")
        return serial, entry.session, entry.manager or _ChannelManager(entry.session)

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            if entry.manager is not None:
                entry.manager.shutdown()
            if entry.session is not None:
                entry.session.close()


def _build_stream_frame(
    manager: _ChannelManager, session: DeviceSession, store: SnapshotStore
) -> dict:
    """预览帧构建:scrcpy 最新帧优先,读取不占租约(测试运行中画面不中断)。

    通道启动仍经租约短事务;通道不可用时回退 u2 截图轮询(该路径需要
    租约,被测试持有时发状态帧,前端以状态行呈现、不遮画面)。
    """
    if session.state not in (SessionState.READY, SessionState.LEASED):
        return {
            "kind": "status",
            "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "device_state": session.state.value,
            "busy": False,
            "message": "设备会话未就绪,画面暂停;重连后自动恢复",
        }
    channel = manager.get()
    if channel is not None:
        if not channel.healthy():
            manager.invalidate("scrcpy 通道不健康(连接断开或泵线程停止)")
        else:
            started = time.monotonic()
            try:
                jpeg, width, height = channel.latest_frame()
                screen_w, screen_h = channel.screen_size()
            except StreamUnavailable as exc:
                manager.invalidate(str(exc))
            else:
                return {
                    "kind": "frame",
                    "source": "scrcpy",
                    "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                    "width": width,
                    "height": height,
                    "screen_width": screen_w,
                    "screen_height": screen_h,
                    "decode_ms": round((time.monotonic() - started) * 1000, 1),
                    "jpeg_base64": base64.b64encode(jpeg).decode("ascii"),
                }
    frame = _build_preview_frame(session, store)
    if frame.get("kind") == "frame":
        frame["source"] = "u2-fallback"
        frame["fallback_reason"] = manager.last_error
    return frame


def create_app(
    session: DeviceSession | None = None,
    store: SnapshotStore | None = None,
    *,
    preview_interval: float = _PREVIEW_INTERVAL_SECONDS,
    stream_interval: float = _STREAM_INTERVAL_SECONDS,
    channel_manager: _ChannelManager | None = None,
    registry: _DeviceRegistry | None = None,
) -> FastAPI:
    """构建网页应用;会话/注册表由调用方注入(lifespan 不拥有启动/关闭)。

    传 session 时构建仅含该设备的兼容注册表(测试与单设备旧调用方);
    多设备 serve 传 registry。channel_manager 仅在单设备模式下生效。
    """
    store = store or SnapshotStore()
    app = FastAPI(title="mobile-auto viewer", lifespan=None)
    app.state.ws_clients = 0
    app.state.device_info_cache = {}  # 设备档案缓存(P2 工单 06):探测一次,进程内不轮询
    app.state.device_info_lock = threading.Lock()
    app.state.store = store
    app.state.registry = registry or _DeviceRegistry.single(
        session, manager=channel_manager
    )
    app.state.preview_interval = preview_interval
    app.state.stream_interval = stream_interval
    # 直接控制(P1 工单 04):显式启用,默认关闭
    app.state.control_enabled = False
    app.state.last_inject = 0.0

    def _resolve(device_param: str | None):
        return app.state.registry.resolve(device_param)

    def _resolve_request(request: Request):
        return _resolve(request.query_params.get("device"))

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

    # ── 状态与设备 ────────────────────────────────────────────

    @app.get("/api/v1/devices")
    async def devices(request: Request):
        """adb 可见设备与已建会话的并集;不创建会话、无副作用。"""
        return {
            "devices": app.state.registry.devices(),
            "request_id": request.state.request_id,
        }

    @app.get("/api/v1/devices/{serial}/info")
    async def device_info(serial: str, request: Request):
        """设备档案(P2 工单 06):品牌/型号/系统版本/分辨率。

        只读探测(getprop + wm size),结果进程内缓存不轮询;仅已授权在线
        设备探测,未授权/离线返回空档案不触碰 adb;探测失败各字段为
        null,不阻塞设备列表。
        """
        registry: _DeviceRegistry = app.state.registry
        states = {d["serial"]: d["state"] for d in registry.devices()}
        if serial not in states:
            return error_envelope(request, 404, "not-found", f"设备 {serial} 不在已知列表")
        cache: dict = app.state.device_info_cache
        with app.state.device_info_lock:
            cached = cache.get(serial)
        if cached is not None:
            return {"serial": serial, **cached}
        if states[serial] != "device":
            empty = _empty_device_info()
        else:
            empty = await asyncio.to_thread(_probe_device_info, serial)
        with app.state.device_info_lock:
            cache[serial] = empty
        return {"serial": serial, **empty}

    @app.get("/api/v1/status")
    async def status(request: Request, device: str | None = None):
        registry: _DeviceRegistry = app.state.registry
        serial = device or registry.default_serial()
        entry = registry.entry(serial) if serial else None  # 不触发连接
        session = entry.session if entry is not None else None
        return {
            "service_version": __version__,
            "request_id": request.state.request_id,
            "device": {
                "serial": serial,
                "state": session.state.value if session is not None else "disconnected",
                "lease_held": session is not None and session.state == SessionState.LEASED,
                "busy": session is not None and session.state == SessionState.LEASED,
            },
            "devices": registry.devices(),
            "capture": {
                "can_capture": session is not None and session.state == SessionState.READY,
                "snapshots": len(store.snapshot_ids()),
            },
        }

    # ── 失败证据浏览(P2 工单 08;只读,限 artifacts 内)─────

    @app.get("/api/v1/evidence")
    async def evidence_list(request: Request):
        """证据清单:artifacts 下任意层级(xdist worker 子目录)的 evidence 条目。"""
        items = await asyncio.to_thread(_list_evidence, app.state.registry.config)
        return {"evidence": items, "request_id": request.state.request_id}

    @app.get("/api/v1/evidence/{evidence_id}")
    async def evidence_detail(evidence_id: str, request: Request):
        meta = await asyncio.to_thread(
            _read_evidence_meta, app.state.registry.config, evidence_id
        )
        if meta is None:
            return error_envelope(request, 404, "not-found", "证据不存在")
        return meta

    @app.get("/api/v1/evidence/{evidence_id}/files/{name}")
    async def evidence_file(evidence_id: str, name: str, request: Request):
        served = await asyncio.to_thread(
            _read_evidence_file, app.state.registry.config, evidence_id, name
        )
        if served is None:
            return error_envelope(request, 404, "not-found", "证据文件不存在")
        payload, media_type = served
        return Response(
            content=payload,
            media_type=media_type,
            headers={"cache-control": "no-store"},
        )

    # ── 占用与运行态(P2 工单 07;只读)─────────────────────

    @app.get("/api/v1/leases")
    async def leases(request: Request):
        """设备占用可视:probe_held 判定当前占用,伴生文件呈现持有者。

        空闲设备不返回持有者(伴生内容是上次获取的残留)。
        """
        registry: _DeviceRegistry = app.state.registry
        serials = [d["serial"] for d in registry.devices()]
        result = await asyncio.to_thread(_probe_leases, serials)
        return {"leases": result, "request_id": request.state.request_id}

    @app.get("/api/v1/runs")
    async def runs(request: Request):
        """各 worker 的运行状态(池化运行写入 artifacts/runs;心跳过期标失联)。"""
        from ..pool import read_run_states

        states = await asyncio.to_thread(
            read_run_states, Path(app.state.registry.config.artifacts_dir) / "runs"
        )
        return {"runs": states, "request_id": request.state.request_id}

    # ── 快照 ──────────────────────────────────────────────────

    @app.post("/api/v1/snapshots")
    async def create_snapshot(request: Request, device: str | None = None):
        body = await request.json() if await request.body() else {}
        with_tree = bool(body.get("with_tree", False))
        _, session, _ = _resolve(device)
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
    async def create_template(request: Request, device: str | None = None):
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
        _, session, _ = _resolve(device)
        try:
            loaded = await asyncio.to_thread(
                _crop_and_register, session, snapshot, logical, region, body, None
            )
        except MobileAutoError as exc:
            status = 409 if isinstance(exc, StaleHandle) else _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return _template_payload(loaded)

    @app.put("/api/v1/templates/{logical:path}")
    async def update_template(logical: str, request: Request, device: str | None = None):
        """确认更新:期望旧摘要与新裁剪参数;摘要不符返回冲突(409)。"""
        body = await request.json()
        snapshot = store.get(body.get("snapshot_id", ""))
        if snapshot is None:
            return error_envelope(request, 404, "not-found", "快照不存在或已过期")
        expected = str(body.get("expected_old_sha256") or "")
        _, session, _ = _resolve(device)
        try:
            loaded = await asyncio.to_thread(
                _crop_and_register, session, snapshot, logical, body.get("region"), body, expected
            )
        except MobileAutoError as exc:
            status = 409 if isinstance(exc, StaleHandle) else _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return _template_payload(loaded)

    @app.get("/api/v1/templates")
    async def list_templates(request: Request, device: str | None = None):
        from ..imaging.store import TemplateStore

        _, session, _ = _resolve(device)
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
    async def template_image(logical: str, request: Request, device: str | None = None):
        from ..imaging.store import TemplateStore

        _, session, _ = _resolve(device)
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
    async def validate_template(request: Request, device: str | None = None):
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
        _, session, _ = _resolve(device)
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
    async def validate_locator(request: Request, device: str | None = None):
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
        _, session, _ = _resolve(device)
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
        try:
            _, session, manager = _resolve(ws.query_params.get("device"))
        except MobileAutoError as exc:
            await ws.accept()
            await ws.send_json({
                "kind": "status",
                "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "busy": False,
                "message": f"设备不可用:{exc}",
            })
            await ws.close(code=4404)
            return
        app.state.ws_clients += 1
        try:
            if manager.clients >= _MAX_WS_CLIENTS_PER_DEVICE:
                await ws.close(code=4429)
                return
            manager.client_connected()
            await ws.accept()
            sender = _PreviewSender(ws)
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

    def _inject(session: DeviceSession, manager: _ChannelManager, operation):
        """短事务注入:租约(409 门控)→ 队列串行执行 → 释放。

        会话与通道管理器按请求解析的目标设备传入;视频通道懒启动遵循
        同一租约门控;超时且任务仍在运行时,租约保留至任务实际结束
        (经 _LeaseGuard 移交),失败时如实报错。
        """
        with _LeaseGuard(session) as guard:
            channel = manager.get()
            if channel is None or not channel.healthy():
                raise StreamUnavailable("视频流通道不可用,无法注入")
            _submit_bounded(
                session,
                guard,
                lambda backend: operation(channel),
                timeout=10.0,
                error_message="注入执行超时",
            )

    @app.post("/api/v1/input/touch")
    async def input_touch(request: Request, device: str | None = None):
        body = await request.json()
        gate = _gate_control(request)
        if gate is not None:
            return gate
        values = _normalized(body, ("nx", "ny"))
        if values is None:
            return error_envelope(request, 422, "invalid-input", "nx/ny 必须是数值")
        nx, ny = values
        _, session, manager = _resolve(device)
        try:
            await asyncio.to_thread(
                _inject,
                session,
                manager,
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
    async def input_swipe(request: Request, device: str | None = None):
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
        _, session, manager = _resolve(device)
        try:
            await asyncio.to_thread(
                _inject,
                session,
                manager,
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
    async def input_key(request: Request, device: str | None = None):
        body = await request.json() if await request.body() else {}
        gate = _gate_control(request)
        if gate is not None:
            return gate
        name = str(body.get("name") or "")
        _, session, manager = _resolve(device)
        try:
            await asyncio.to_thread(
                _inject, session, manager, lambda channel: channel.press_key(name)
            )
        except MobileAutoError as exc:
            status = _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return {"ok": True, "request_id": request.state.request_id}

    @app.post("/api/v1/input/wake")
    async def input_wake(request: Request, device: str | None = None):
        gate = _gate_control(request)
        if gate is not None:
            return gate
        _, session, manager = _resolve(device)
        try:
            await asyncio.to_thread(
                _inject, session, manager, lambda channel: channel.wake()
            )
        except MobileAutoError as exc:
            status = _ERROR_STATUS.get(type(exc), 400)
            return error_envelope(request, status, type(exc).__name__, str(exc))
        return {"ok": True, "request_id": request.state.request_id}

    app.state.static_dir = _STATIC_DIR
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


class _PreviewSender:
    """每客户端仅保留最新帧:上一帧未发完则丢弃新帧,不堆积。

    发送带超时:客户端停止读取时 TCP 背压会让 send 永久挂起,
    连接既不断开也不再出帧,页面冻结在最后一帧。超时判定失联,
    抛 RuntimeError 交给预览循环退出,槽位释放、客户端自动重连。
    """

    _SEND_TIMEOUT_SECONDS = 5.0

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
            await asyncio.wait_for(
                self._ws.send_json(frame), timeout=self._SEND_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"预览客户端 {self._SEND_TIMEOUT_SECONDS:.0f} 秒未取帧,判定失联"
            ) from exc
        finally:
            self._busy = False


def _build_preview_frame(session: DeviceSession, store: SnapshotStore) -> dict:
    """u2 回退帧:仅观察;pytest 持有租约(LEASED)时不主动采集。"""
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    if session.state != SessionState.READY:
        busy = session.state == SessionState.LEASED
        return {
            "kind": "status",
            "captured_at": now,
            "device_state": session.state.value,
            "busy": busy,
            "message": (
                "视频流不可用且设备忙(测试运行中);测试结束后自动恢复截图轮询"
                if busy
                else "设备未就绪,画面暂停;恢复后自动继续"
            ),
        }
    try:
        # 预览帧是瞬态观察,不入快照库:不挤占拾取/验证用的用户快照
        with _LeaseGuard(session) as guard:
            png = _submit_bounded(
                session,
                guard,
                lambda backend: backend.screenshot_png(),
                timeout=_CAPTURE_TIMEOUT_SECONDS,
                error_message="预览截图超时",
            )
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


class _LeaseGuard:
    """短事务租约看护:保证租约释放不早于队列任务实际结束。

    正常完成(以及超时但任务已结束)由 with 块退出释放;超时而任务
    仍在运行时,把释放责任移交给任务完成回调——单 worker 队列里运行中
    的任务无法中断,提前释放会让其他进程在设备仍被操作时获得租约。
    """

    def __init__(self, session: DeviceSession) -> None:
        self._session = session
        self._lock = threading.Lock()
        self._settled = False  # 已释放或已移交,二者只发生一次

    def hand_off_to(self, future) -> None:
        """移交释放责任:future 完成时释放(已在完成则立即释放)。"""
        with self._lock:
            if self._settled:
                return
            self._settled = True

        def _release_when_done(_future) -> None:
            try:
                # close 并发排空时由 close 自己释放;此处只补常态释放
                if self._session.state == SessionState.LEASED:
                    self._session.release_lease()
            except Exception:  # 回调线程内失败不影响任务结果
                pass

        future.add_done_callback(_release_when_done)

    def release(self) -> None:
        with self._lock:
            if self._settled:
                return
            self._settled = True
        self._session.release_lease()

    def __enter__(self) -> _LeaseGuard:
        self._session.acquire_lease()  # LEASED(pytest 持有)→ DeviceBusy → 409
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def _submit_bounded(session, guard, operation, *, timeout: float, error_message: str):
    """提交队列并 bounded 等待;超时且任务仍在运行时,租约经 guard
    移交给完成回调,保留占用至任务结束。"""
    future = session.submit(operation)
    try:
        return future.result(timeout=timeout)
    except TimeoutError as exc:
        session.mark_cancelled(future)  # 排队任务撤销;运行任务结局记 unknown
        if not future.done():
            guard.hand_off_to(future)  # 运行中任务无法中断:保留租约至其结束
        raise DeviceUnavailable(error_message) from exc
    finally:
        guard.release()


def capture_snapshot_sync(session: DeviceSession, with_tree: bool) -> Snapshot:
    """短事务采集:获取租约 → 截图(可选树)→ 释放;有界等待。"""
    with _LeaseGuard(session) as guard:
        if session.state != SessionState.LEASED:
            raise DeviceUnavailable("会话未就绪,无法采集")
        payload = _submit_bounded(
            session,
            guard,
            lambda backend: _capture_payload(backend, with_tree),
            timeout=_CAPTURE_TIMEOUT_SECONDS,
            error_message="采集超时",
        )
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

    with _LeaseGuard(session) as guard:
        payload = _submit_bounded(
            session,
            guard,
            lambda backend: _query_payload(backend, session, store, target),
            timeout=_CAPTURE_TIMEOUT_SECONDS,
            error_message="定位验证查询超时",
        )
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
    from ..imaging.engine import match_template, validate_capture_center
    from ..imaging.store import TemplateStore

    png = backend.screenshot_png()
    screen = backend.screen_size()
    scores = None
    if target.by.value == "image":
        template = TemplateStore(session.config.assets_dir).load(target.value)
        source = (
            template.meta.get("source_width"),
            template.meta.get("source_height"),
        )
        results = match_template(
            png,
            template.png,
            confidence=target.confidence,
            region=target.region,
            source_resolution=(
                source if all(isinstance(v, int) for v in source) else None
            ),
            capture_center=validate_capture_center(
                template.meta.get("capture_center")
            ),
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
    """服务端从保留快照裁剪原始像素,经资产库 create/update 登记。

    采集中心(P4b)由裁剪区域相对快照中心换算并自动写入 meta,
    匹配侧据此先在预测区域搜索(未命中回退全区域)。
    """
    import numpy as np

    from ..imaging.engine import capture_center_from_region, decode_rgb
    from ..imaging.store import TemplateStore

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
    capture_center = capture_center_from_region(region, (w, h))
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
            capture_center=capture_center,
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
        capture_center=capture_center,
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
    """解析后精确比对主机名:前缀匹配会把 localhost.attacker.example
    一并放行;端口任意(本机开发端口不固定),主机名必须恰好是回环。
    畸形来源(如非法 IPv6 括号)解析失败一律拒绝。"""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    return parts.hostname in ("127.0.0.1", "localhost", "::1")
