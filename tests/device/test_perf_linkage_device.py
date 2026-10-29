"""M4 联动真机用例（ticket 11）：UI 驱动 bili 滑动 + mobile_perf 采集 + 门禁结论。

依赖方向（ADR-0001）：mobile_auto → mobile_perf。PerfDeviceGate 把本包
DeviceSession 持有的整生命周期租约桥接为 mobile_perf 短事务 DeviceGate
（嵌入模式重入放行，序列化由会话队列保证）；采集窗口用 mobile_perf 的
PerfFixture（与 ``--mobile-perf`` 钩子同一接缝）做 mark/finalize/judge。
被测应用前台由 mobile_perf 的前台守卫在 SoloPi 抢前台后还原。

零人工干预运行（真机在线、已装 B 站与自编译 SoloPi）::

    python -m pytest tests/device/test_perf_linkage_device.py -m device \\
        --mobile-config tests/device/local.toml

无配置/无设备时跳过，不计为通过。
"""

import asyncio

import pytest

pytest.importorskip("mobile_perf")  # 未安装 mobile_perf:整模块跳过,不阻断收集

from mobile_auto._perf_gate import PerfDeviceGate  # noqa: E402

pytestmark = pytest.mark.device

TARGET_PACKAGE = "tv.danmaku.bili"
BASELINE_NAME = "bili-linkage"


@pytest.fixture
def perf_gate(mobile_session):
    """宿主租约 → mobile_perf DeviceGate（嵌入模式）。"""
    lease = getattr(mobile_session, "_lease", None)
    if lease is None or not lease.held:
        pytest.skip("mobile_session 未持有租约，联动用例未执行")
    return PerfDeviceGate(leases={mobile_session.config.device_serial: lease})


@pytest.fixture
def perf(mobile_session, perf_gate, tmp_path, request):
    """mobile_perf PerfFixture：与 --mobile-perf 钩子同一 perf.session 接缝。"""
    from mobile_perf import PerfKit
    from mobile_perf.pytest_plugin import PerfFixture

    kit = PerfKit(
        serial=mobile_session.config.device_serial,
        runs_dir=tmp_path / "runs",
        db_path=tmp_path / "data" / "perf.db",
        gate=perf_gate,
        device_profile="LinkageDeviceTest",
        keep_runs=10,
    )
    return PerfFixture(kit, request)


def _swipe_up(mobile_session, pixels: int = 400) -> None:
    """经会话队列用 u2 后端上滑（本机 scrcpy 通道与 swipe 手势的适配
    缺口是宿主既有问题，不属本票；滑动仍由 mobile-auto 驱动 UI）。"""

    def op(backend):
        w, h = backend.screen_size()
        y0 = int(h * 0.7)
        backend.swipe(w // 2, y0, w // 2, max(0, y0 - pixels), 0.3)

    mobile_session.submit(op).result(timeout=30)


@pytest.mark.device
async def test_ui_swipe_collect_and_gate_judgement(mobile_session, perf):
    # 冷启动被测应用：前台守卫的还原基线即 bili
    launch = mobile_session.submit(
        lambda backend: backend.app_start(TARGET_PACKAGE, None, stop=True)
    )
    launch.result(timeout=60)
    await asyncio.sleep(3)

    # 第一轮：UI 驱动滑动 + 采集（作为基线 run）
    with perf.session(
        TARGET_PACKAGE, items=["CPU", "Memory"], host_run_id="linkage-baseline"
    ) as scope:
        _swipe_up(mobile_session)
        scope.mark("baseline_scroll_done")
        await asyncio.sleep(6)
    baseline = scope.report
    assert baseline["metricsSummary"], "第一轮采集应产出指标聚合"
    assert [m["label"] for m in baseline["marks"]] == ["baseline_scroll_done"]

    # 基线入库 → 第二轮带阈值判读
    perf.kit.save_baseline(baseline, BASELINE_NAME)
    with perf.session(
        TARGET_PACKAGE,
        items=["CPU", "Memory"],
        host_run_id="linkage-judged",
        baseline=BASELINE_NAME,
        thresholds={
            "mem_pss_mb": ("lt", 600, "fail"),
            "cpu_process_pct": ("lt", 95, "fail"),
        },
        min_samples=3,
    ) as judged_scope:
        _swipe_up(mobile_session)
        judged_scope.mark("judged_scroll_done")
        await asyncio.sleep(6)

    result = perf.results[-1]
    assert result.verdict is not None, f"第二轮应产出判读结论：{result.section_text()}"
    assert result.verdict.verdict in {"passed", "failed"}
    assert result.verdict.checks, "判读应包含阈值检查明细"
