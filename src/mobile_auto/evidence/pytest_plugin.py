"""pytest 失败证据钩子(P1 工单 06)+ 设备池/运行态钩子(P2 工单 05)。

--mobile-evidence:opt-in,不开启零行为。用例 call 阶段失败时,在本进程
funcargs 中寻找可用的 DeviceSession(优先名为 mobile_session 的 fixture,
其次任意 DeviceSession 实例),调用失败取证并把证据路径写入
report.sections,兼容 pytest-html / junit 的 section 展示。取证失败不
影响原始失败上报。会话生命周期归属调用方;本插件只读使用会话。

设备池(P2 工单 05):--mobile-devices 或配置 device_pool 启用池化
mobile_session/mobile_adapter fixture——以跨进程租约为协调器非阻塞抢
设备,xdist 下每 worker 各持一台、产物按 worker 分目录;池未配置时
fixture 显式报错(不静默)。池化运行期间写 artifacts/runs/<worker>.json
运行状态(工单 07 消费);conftest 同名 fixture 仍可整体覆盖。
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

_SECTION_TITLE = "mobile-auto-evidence"
_POOL_STATE: dict = {}  # 池化运行态(sessionstart 建立,finish 清空)
_DEBUG_VIEWER = None  # DebugViewerHandle(sessionstart 建立,finish 关停)


def pytest_addoption(parser):
    group = parser.getgroup("mobile-auto")
    group.addoption(
        "--mobile-evidence",
        action="store_true",
        default=False,
        help="用例失败时自动采集设备画面与 UI 树证据(mobile-auto)",
    )
    group.addoption(
        "--mobile-devices",
        action="store",
        default=None,
        metavar="SERIALS",
        help="设备池候选(逗号分隔,覆盖配置 device_pool;启用池化 mobile_session)",
    )
    group.addoption(
        "--debug-viewer",
        action="store_true",
        default=False,
        help="pytest 运行期间拉起本机 viewer 调试服务(仅 controller 进程;默认关闭)",
    )


# ── 设备池 fixture(P2 工单 05)───────────────────────────


def _optional_getoption(config, name):
    """读取可能由其他插件/conftest 定义的选项;未定义返回 None。"""
    try:
        return config.getoption(name, default=None)
    except ValueError:
        return None


def _resolve_pool(pytest_config):
    """旗标 --mobile-devices > TOML device_pool > 单值 device_serial。"""
    from ..config import load_config

    flag = pytest_config.getoption("--mobile-devices")
    if flag:
        pool = tuple(s.strip() for s in flag.split(",") if s.strip())
    else:
        config_path = _optional_getoption(pytest_config, "--mobile-config")
        base = load_config(config_path)
        pool = base.device_pool or (
            (base.device_serial,) if base.device_serial else ()
        )
    return pool


def _pool_base_config(pytest_config):
    from ..config import load_config

    config_path = _optional_getoption(pytest_config, "--mobile-config")
    return load_config(config_path)


def _enable_journal(session, enabled: bool):
    """跟随 --mobile-evidence 挂接动作流水;关闭时零行为、不建文件。

    路径由会话的 artifacts_dir 决定(池化时已是 xdist worker 目录),
    与失败证据同源;文件本身懒创建,首次记录才落盘。
    """
    if not enabled:
        return None
    from .journal import Journal

    journal = Journal(session.config.artifacts_dir, session.session_id)
    session.set_journal(journal)
    return journal


@pytest.fixture(scope="session")
def mobile_session(request):
    """池化设备会话:抢池内一台设备的租约并绑定会话。

    xdist worker 各自独立进程抢不同租约;artifacts 按 worker 分目录,
    运行清单与失败证据互不覆盖。池未配置时报错(不静默回退单设备)。
    """
    from ..core.session import DeviceSession
    from ..pool import acquire_from_pool, worker_artifacts_dir, write_run_state

    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    pool = _resolve_pool(request.config)
    if not pool:
        pytest.fail(
            "mobile_session 需要设备池:提供 --mobile-devices 或在配置中"
            "声明 device_pool(不静默回退)",
            pytrace=False,
        )
    base = _pool_base_config(request.config)
    serial, lease = acquire_from_pool(pool, worker=worker or "main")
    config = dataclasses.replace(
        base,
        device_serial=serial,
        artifacts_dir=worker_artifacts_dir(base.artifacts_dir, worker),
    )
    session = DeviceSession(config, lease_factory=lambda _serial: lease)
    try:
        session.start()  # acquire 幂等:复用池已获取的租约
    except BaseException:
        lease.release()  # resolve 阶段抛错时 start 不触碰租约:补释放防泄漏
        raise
    _POOL_STATE["serial"] = serial
    write_run_state(
        base.artifacts_dir / "runs",  # 状态文件固定在基础产物目录(工单 07 统一读)
        worker=worker,
        phase="running",
        serial=serial,
    )
    _enable_journal(session, request.config.getoption("--mobile-evidence"))
    yield session
    session.close()  # 释放租约;释放不回滚已发送的操作
    if session.journal is not None:
        session.journal.close()
    write_run_state(
        base.artifacts_dir / "runs", worker=worker, phase="finished", serial=serial
    )


@pytest.fixture
def mobile_adapter(mobile_session):
    """function 级包装器:用例结束只释放包装器,会话由 mobile_session 持有。"""
    from ..core.adapter import MobileAdapter

    return MobileAdapter(mobile_session)


# ── 运行状态记录(工单 07 数据源;仅池化运行时写)─────────


def pytest_sessionstart(session):
    global _POOL_STATE, _DEBUG_VIEWER
    if not _resolve_pool(session.config):
        _POOL_STATE = {}
    else:
        _POOL_STATE = {
            "worker": os.environ.get("PYTEST_XDIST_WORKER", ""),
            "config": session.config,
            "base": _pool_base_config(session.config),
            "serial": None,
        }
        _write_state(phase="started")
    if session.config.getoption("--debug-viewer") and not os.environ.get(
        "PYTEST_XDIST_WORKER"
    ):
        # 仅 controller/主进程启动:xdist worker 不再各起一份抢端口(P2 工单 03)
        from ..errors import ConfigurationError
        from ..viewer.server import start_debug_viewer

        try:
            _DEBUG_VIEWER = start_debug_viewer(_pool_base_config(session.config))
        except ConfigurationError as exc:
            raise pytest.UsageError(f"--debug-viewer 启动失败:{exc}") from exc
        print(
            f"\nmobile-auto viewer 运行中:{_DEBUG_VIEWER.url}"
            "(实时预览读不占设备租约;会话结束后自动关闭)",
            flush=True,
        )


def pytest_runtest_logstart(nodeid, location):
    if _POOL_STATE:
        _write_state(phase="running", current_test=nodeid)


def pytest_sessionfinish(session, exitstatus):
    global _DEBUG_VIEWER
    if _POOL_STATE:
        _write_state(phase="finished")
        _POOL_STATE.clear()
    if _DEBUG_VIEWER is not None:
        _DEBUG_VIEWER.stop()
        _DEBUG_VIEWER = None


def _write_state(*, phase: str, current_test: str | None = None) -> None:
    from ..pool import write_run_state

    write_run_state(
        _POOL_STATE["base"].artifacts_dir / "runs",
        worker=_POOL_STATE["worker"],
        phase=phase,
        serial=_POOL_STATE.get("serial"),
        current_test=current_test,
    )


def _find_session(item) -> object | None:
    """从用例 funcargs 中提取 DeviceSession:优先 mobile_session 命名。"""
    funcargs = getattr(item, "funcargs", {}) or {}
    named = funcargs.get("mobile_session")
    if named is not None:
        return named
    from ..core.session import DeviceSession

    for value in funcargs.values():
        if isinstance(value, DeviceSession):
            return value
    return None


def _section_text(record: dict) -> str:
    frame_info = record["frame_source"] or {}
    lines = [
        f"evidence_id: {record['evidence_id']}",
        f"frame_source: {frame_info.get('source')}",
    ]
    if frame_info:
        lines.append(f"capture_lag_ms: {frame_info.get('capture_lag_ms')}")
    lines.append("artifacts:")
    for artifact in record["artifacts"]:
        lines.append(f"  - {artifact['name']} ({artifact['kind']})")
    for missed in record["not_captured"]:
        lines.append(f"  ! 未采集 {missed['kind']}: {missed['reason']}")
    return "\n".join(lines)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    if not item.config.getoption("--mobile-evidence"):
        return
    session = _find_session(item)
    if session is None:
        report.sections.append(
            (
                _SECTION_TITLE,
                "未找到可用的 DeviceSession(提供 mobile_session fixture 或"
                "任何 DeviceSession funcarg),未采集证据",
            )
        )
        return
    from .collector import collect_failure_evidence

    try:
        record = collect_failure_evidence(session, str(report.longreprtext))
    except Exception as exc:  # 取证失败不覆盖原始测试错误
        report.sections.append((_SECTION_TITLE, f"取证失败:{exc!r}"))
        return
    text = _section_text(record)
    meta_path = (
        __import__("pathlib").Path(session.config.artifacts_dir)
        / "evidence"
        / record["evidence_id"]
        / "meta.json"
    )
    report.sections.append((_SECTION_TITLE, f"{text}\nmeta: {meta_path}"))
    # 证据条目同时写入 junitxml 的 system-out(经由 sections 机制的用户侧展示
    # 由报告插件负责);此处额外挂到 user_properties 便于自定义报告读取。
    report.user_properties.append(
        ("mobile_auto_evidence", json.dumps(record["frame_source"] or {}))
    )
