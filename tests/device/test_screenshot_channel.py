"""驱动截图双通道真机验证(工单 03):匹配一致性基线与时延对比。

一致性:同一静态画面分别以 u2 与 scrcpy 截图跑模板匹配,数量一致、
边界偏差 ≤1px、置信度差 ≤0.005(工单 03 验收);不达标时 auto 策略
的 scrcpy 路径应在配置中显式关闭并记录(此处仅断言结论)。
"""

import cv2
import numpy as np
import pytest

from mobile_auto import Locator, Target
from mobile_auto.imaging.engine import decode_rgb, match_template
from mobile_auto.imaging.store import TemplateStore

pytestmark = pytest.mark.device


def png_bytes(arr_rgb) -> bytes:
    ok, buf = cv2.imencode(".png", arr_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


@pytest.mark.device
async def test_screenshot_source_scrcpy_returns_native_png(adapter):
    """强制 scrcpy:CAPTURE 原生分辨率且可解码。"""
    await adapter.goto("")
    width, height = adapter._session._backend.screen_size()
    adapter._session.config  # noqa: B018  # 冻结引用便于阅读
    png = await _forced_scrcpy_screenshot(adapter)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape[:2] == (height, width)


@pytest.mark.device
async def test_match_consistency_u2_vs_scrcpy(adapter, mobile_config):
    """固定样本一致性:u2 与 scrcpy 截图的匹配结果一致(工单 03 基线)。"""
    await adapter.goto("")
    wlan_row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(wlan_row) is True
    (row,) = await adapter.find_elements(wlan_row)
    left, top, right, bottom = row.bounds

    u2_png = await adapter.screenshot()  # auto 默认,首屏即 scrcpy?显式分别取:
    scrcpy_png = await _forced_scrcpy_screenshot(adapter)
    u2_png = await _forced_u2_screenshot(adapter)

    frame = decode_rgb(u2_png, what="u2 截图")
    x0, y0 = max(0, left - 130), max(0, top - 15)
    x1, y1 = min(1080, right + 60), min(2400, bottom + 15)
    template_png = png_bytes(frame[y0:y1, x0:x1])
    store = TemplateStore(mobile_config.assets_dir)
    logical = "consistency/probe.png"
    existing = store.list_templates().get(logical)
    if existing:
        store.update(
            logical,
            template_png,
            existing["image_sha256"],
            source_width=1080,
            source_height=2400,
            orientation="portrait",
            app_package="com.android.settings",
            note="工单03一致性基线",
        )
    else:
        store.create(
            logical,
            template_png,
            source_width=1080,
            source_height=2400,
            orientation="portrait",
            app_package="com.android.settings",
            note="工单03一致性基线",
        )
    results_u2 = match_template(u2_png, template_png, confidence=0.80)
    results_scrcpy = match_template(scrcpy_png, template_png, confidence=0.80)
    assert results_u2, "u2 截图零匹配,样本无效"
    assert len(results_u2) == len(results_scrcpy), (
        f"匹配数量不一致:u2={len(results_u2)}, scrcpy={len(results_scrcpy)}"
    )
    for a, b in zip(results_u2, results_scrcpy, strict=True):
        assert abs(a.x - b.x) <= 1 and abs(a.y - b.y) <= 1, f"边界偏差超限:{a} vs {b}"
        assert abs(a.confidence - b.confidence) <= 0.005, (
            f"置信度差超限:{a.confidence} vs {b.confidence}"
        )


@pytest.mark.device
async def test_capture_latency_comparison_recorded(adapter):
    """时延对比(记录用):u2 与 scrcpy 各 10 次,打印中位数供验证记录引用。"""
    import time

    await adapter.goto("")

    async def median_ms(forced):
        times = []
        for _ in range(10):
            t0 = time.monotonic()
            await forced(adapter)
            times.append((time.monotonic() - t0) * 1000)
        return sorted(times)[len(times) // 2]

    u2_ms = await median_ms(_forced_u2_screenshot)
    scrcpy_ms = await median_ms(_forced_scrcpy_screenshot)
    print(f"\n[latency] u2 中位数 {u2_ms:.0f}ms,scrcpy 中位数 {scrcpy_ms:.0f}ms")
    assert scrcpy_ms < u2_ms, (
        f"scrcpy 截图({scrcpy_ms:.0f}ms)未快于 u2({u2_ms:.0f}ms);"
        "如持续如此,auto 默认值需要在验证记录中复核"
    )


async def _forced_u2_screenshot(adapter) -> bytes:
    def operation(backend):
        return backend.screenshot_png()

    return await adapter._call(operation)


async def _forced_scrcpy_screenshot(adapter) -> bytes:
    def operation(backend):
        return adapter._session.get_or_start_channel().capture_png()

    return await adapter._call(operation)
