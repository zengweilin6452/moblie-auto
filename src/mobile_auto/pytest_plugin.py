"""pytest 失败证据钩子(P1 工单 06):opt-in,不开启零行为。

用法:pytest --mobile-evidence(内网或本地接入时挂上即可)。
用例 call 阶段失败时,在本进程 funcargs 中寻找可用的 DeviceSession
(优先名为 mobile_session 的 fixture,其次任意 DeviceSession 实例),
调用失败取证并把证据路径写入 report.sections,兼容 pytest-html /
junit 的 section 展示。取证失败不影响原始失败上报。

会话生命周期归属调用方(通常为 session 级 fixture);本插件只读
使用会话,不负责关闭。
"""

from __future__ import annotations

import json

import pytest

_SECTION_TITLE = "mobile-auto-evidence"


def pytest_addoption(parser):
    group = parser.getgroup("mobile-auto")
    group.addoption(
        "--mobile-evidence",
        action="store_true",
        default=False,
        help="用例失败时自动采集设备画面与 UI 树证据(mobile-auto)",
    )


def _find_session(item) -> object | None:
    """从用例 funcargs 中提取 DeviceSession:优先 mobile_session 命名。"""
    funcargs = getattr(item, "funcargs", {}) or {}
    named = funcargs.get("mobile_session")
    if named is not None:
        return named
    from .session import DeviceSession

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
    from .evidence import collect_failure_evidence

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
