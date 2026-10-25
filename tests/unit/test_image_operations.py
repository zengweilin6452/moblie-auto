"""IMAGE 驱动操作的外部行为:事务点击、句柄重校验、不支持矩阵与取消窗口。

截图以脚本化合成图注入,匹配走真实引擎;模板存入临时资产库。
"""

import asyncio
import threading

import cv2
import numpy as np
import pytest
from fakes import FakeBackend, make_adapter

from mobile_auto import (
    AmbiguousTarget,
    DeviceUnavailable,
    ElementNotFound,
    InvalidTemplate,
    Locator,
    MobileConfig,
    StaleHandle,
    Target,
    UnsupportedOperation,
)
from mobile_auto.imaging.engine import capture_center_from_region
from mobile_auto.imaging.store import TemplateStore

SCREEN_W, SCREEN_H, SIZE = 150, 200, 20
PATTERN = np.random.default_rng(7).integers(0, 255, size=(SIZE, SIZE, 3), dtype=np.uint8)
PATCH_AT = (100, 50)


def png_bytes(arr) -> bytes:
    ok, buf = cv2.imencode(".png", arr[:, :, ::-1])
    assert ok
    return buf.tobytes()


def screen_with(*coords, seed=1):
    screen = np.random.default_rng(seed).integers(
        0, 255, size=(SCREEN_H, SCREEN_W, 3), dtype=np.uint8
    )
    for x, y in coords:
        screen[y : y + SIZE, x : x + SIZE] = PATTERN
    return screen


class ScriptedBackend(FakeBackend):
    """screenshot_png 依次弹出脚本截图,耗尽后停在最后一帧。"""

    def __init__(self, screens, **kwargs):
        super().__init__(screen=(SCREEN_W, SCREEN_H), **kwargs)
        self._screens = [png_bytes(s) for s in screens]

    def screenshot_png(self):
        self.screenshot_calls += 1
        if len(self._screens) > 1:
            return self._screens.pop(0)
        return self._screens[0]


@pytest.fixture
def env(tmp_path):
    assets = tmp_path / "assets"
    store = TemplateStore(assets)
    loaded = store.create(
        "demo/target.png",
        png_bytes(PATTERN),
        source_width=SCREEN_W,
        source_height=SCREEN_H,
        app_package="com.demo",
    )
    config = MobileConfig(device_serial="SERIAL1", assets_dir=assets)
    image_target = Target(Locator.IMAGE, "demo/target.png")
    return store, loaded, config, image_target


def make(config, backend):
    adapter, backend_ref = make_adapter(config=config, backend=backend)
    return adapter, backend_ref


async def test_find_elements_returns_image_handles(env):
    store, loaded, config, image_target = env
    adapter, backend = make(
        config, ScriptedBackend([screen_with(PATCH_AT), screen_with(PATCH_AT)])
    )
    handles = await adapter.find_elements(image_target)
    assert [(h.bounds) for h in handles] == [(100, 50, SIZE, SIZE)]
    assert handles[0].image_sha256 == loaded.sha256
    assert handles[0].screen_size == (SCREEN_W, SCREEN_H)
    assert handles[0].orientation == "portrait"


async def test_find_elements_no_match_is_empty_query(env):
    _, _, config, image_target = env
    adapter, _ = make(config, ScriptedBackend([screen_with()]))
    assert await adapter.find_elements(image_target) == []
    assert await adapter.is_visible(image_target) is False


async def test_find_elements_index_semantics(env):
    _, _, config, image_target = env
    two = screen_with(PATCH_AT, (10, 90))
    adapter, _ = make(config, ScriptedBackend([two]))
    assert len(await adapter.find_elements(image_target)) == 2
    picked = await adapter.find_elements(Target(Locator.IMAGE, image_target.value, index=1))
    assert [h.bounds for h in picked] == [(10, 90, SIZE, SIZE)]


async def test_wait_for_image_appears_later(env):
    _, _, config, image_target = env
    config = MobileConfig(
        device_serial="SERIAL1",
        assets_dir=config.assets_dir,
        poll_interval=0.02,
    )
    adapter, _ = make(
        config, ScriptedBackend([screen_with(), screen_with(), screen_with(PATCH_AT)])
    )
    assert await adapter.wait_for(image_target) is True


async def test_click_taps_matched_center(env):
    _, _, config, image_target = env
    adapter, backend = make(config, ScriptedBackend([screen_with(PATCH_AT)]))
    await adapter.click(image_target)
    assert backend.taps == [(110, 60)]  # 边界中心


async def test_click_multiple_without_index_ambiguous_not_highest(env):
    _, _, config, image_target = env
    adapter, backend = make(
        config, ScriptedBackend([screen_with(PATCH_AT, (10, 90))])
    )
    with pytest.raises(AmbiguousTarget):
        await adapter.click(image_target)
    assert backend.taps == []  # 不自动取最高分
    await adapter.click(Target(Locator.IMAGE, image_target.value, index=0))
    assert backend.taps == [(110, 60)]


async def test_click_no_match_is_element_not_found(env):
    _, _, config, image_target = env
    adapter, _ = make(config, ScriptedBackend([screen_with()]))
    with pytest.raises(ElementNotFound):
        await adapter.click(image_target)


async def test_image_target_region_limits_search(env):
    _, _, config, image_target = env
    limited = Target(Locator.IMAGE, image_target.value, region=(0, 0, 60, 60))
    adapter, _ = make(config, ScriptedBackend([screen_with(PATCH_AT)]))
    with pytest.raises(ElementNotFound):
        await adapter.click(limited)  # 图案在区域外


async def test_text_operations_rejected_for_image(env):
    _, _, config, image_target = env
    adapter, _ = make(config, ScriptedBackend([screen_with(PATCH_AT)]))
    with pytest.raises(UnsupportedOperation, match="input_text"):
        await adapter.input_text(image_target, "x")
    with pytest.raises(UnsupportedOperation, match="get_text"):
        await adapter.get_text(image_target)
    with pytest.raises(UnsupportedOperation, match="get_attribute"):
        await adapter.get_attribute(image_target, "text")
    with pytest.raises(UnsupportedOperation, match="get_text"):
        handles = await adapter.find_elements(image_target)
        await handles[0].get_text()


async def test_invalid_template_and_device_error_separated(env):
    store, _, config, image_target = env
    unregistered = Target(Locator.IMAGE, "demo/ghost.png")
    adapter, _ = make(config, ScriptedBackend([screen_with(PATCH_AT)]))
    with pytest.raises(InvalidTemplate, match="未登记"):
        await adapter.click(unregistered)

    class BrokenBackend(ScriptedBackend):
        def screenshot_png(self):
            raise DeviceUnavailable("设备掉线")

    adapter2, _ = make(config, BrokenBackend([screen_with(PATCH_AT)]))
    with pytest.raises(DeviceUnavailable):
        await adapter2.is_visible(image_target)


async def test_handle_reuse_clicks_when_stable(env):
    _, _, config, image_target = env
    stable = screen_with(PATCH_AT)
    adapter, backend = make(config, ScriptedBackend([stable, stable, stable]))
    (handle,) = await adapter.find_elements(image_target)
    await handle.click()  # 重新匹配、位置一致 → 点击
    assert backend.taps == [(110, 60)]


async def test_handle_stale_after_layout_shift(env):
    """列表插入/删除重排:同一图案移位,句柄过期而非点错目标。"""
    _, _, config, image_target = env
    before = screen_with(PATCH_AT)
    after = screen_with((10, 120))  # 图案移到新位置
    adapter, backend = make(config, ScriptedBackend([before, after, after]))
    (handle,) = await adapter.find_elements(image_target)
    with pytest.raises(StaleHandle, match="位置偏移"):
        await handle.click()
    assert backend.taps == []


async def test_handle_stale_after_template_update(env):
    store, loaded, config, image_target = env
    adapter, _ = make(
        config,
        ScriptedBackend([screen_with(PATCH_AT)] * 3),
    )
    (handle,) = await adapter.find_elements(image_target)
    store.update(
        "demo/target.png", png_bytes(np.random.default_rng(8).integers(
            0, 255, size=(SIZE, SIZE, 3), dtype=np.uint8)),
        loaded.sha256,
        source_width=SCREEN_W,
        source_height=SCREEN_H,
        app_package="com.demo",
    )
    with pytest.raises(StaleHandle, match="模板已更新"):
        await handle.click()


async def test_handle_stale_after_screen_size_change(env):
    _, _, config, image_target = env
    stable = screen_with(PATCH_AT)
    backend = ScriptedBackend([stable, stable, stable])
    adapter, _ = make(config, backend)
    (handle,) = await adapter.find_elements(image_target)
    backend.screen = (SCREEN_W * 2, SCREEN_H)
    with pytest.raises(StaleHandle, match="尺寸变化"):
        await handle.click()


# ── 采集位置先验模板的句柄重校验(重现审查 P4b 回归)──────────────
# 大屏才能让 150px 先验边距区别于全图;远角实例须落在预测区域外。

WIDE_W, WIDE_H = 600, 800
PRIOR_AT = (400, 300)  # 预测中心实例左上角
FAR_AT = (40, 40)  # 远角同图实例(全图序号更靠前)


class WideScriptedBackend(ScriptedBackend):
    """大屏脚本后端:screen_size 与脚本截图一致,供先验几何与句柄校验。"""

    def screen_size(self):
        return (WIDE_W, WIDE_H)


def wide_screen(*coords, seed=1):
    screen = np.random.default_rng(seed).integers(
        0, 255, size=(WIDE_H, WIDE_W, 3), dtype=np.uint8
    )
    for x, y in coords:
        screen[y : y + SIZE, x : x + SIZE] = PATTERN
    return screen


@pytest.fixture
def prior_env(tmp_path):
    assets = tmp_path / "assets"
    store = TemplateStore(assets)
    region = (PRIOR_AT[0], PRIOR_AT[1], SIZE, SIZE)
    center = capture_center_from_region(region, (WIDE_W, WIDE_H))
    store.create(
        "demo/prior.png",
        png_bytes(PATTERN),
        source_width=WIDE_W,
        source_height=WIDE_H,
        app_package="com.demo",
        capture_center=center,
    )
    config = MobileConfig(device_serial="SERIAL1", assets_dir=assets)
    return config, Target(Locator.IMAGE, "demo/prior.png")


async def test_image_handle_reuse_shares_query_match_params(prior_env):
    """查询走采集位置先验,句柄重校验必须共用同一套匹配参数。

    重现审查场景:近处实例 + 远处同图实例、画面完全未变——重校验若
    全图匹配会选中 (y,x) 更靠前的远角实例,刚查到的句柄点击即报
    StaleHandle;共用先验参数后命中同一实例,正常点击。
    """
    config, image_target = prior_env
    two = wide_screen(PRIOR_AT, FAR_AT)
    adapter, backend = make(config, WideScriptedBackend([two, two, two]))
    (handle,) = await adapter.find_elements(image_target)  # 先验生效:只返回近处实例
    assert (handle.bounds[0], handle.bounds[1]) == PRIOR_AT
    await handle.click()  # 画面未变:重校验命中同一实例,不误报过期
    assert backend.taps == [(PRIOR_AT[0] + SIZE // 2, PRIOR_AT[1] + SIZE // 2)]


async def test_image_handle_stale_when_prior_instance_gone(prior_env):
    """近处实例消失、只剩预测区域外的同图实例:回退全图命中后位置
    一致性拒绝,不重定向到远处实例。"""
    config, image_target = prior_env
    both = wide_screen(PRIOR_AT, FAR_AT)
    gone = wide_screen(FAR_AT, seed=3)
    adapter, backend = make(config, WideScriptedBackend([both, gone, gone]))
    (handle,) = await adapter.find_elements(image_target)
    with pytest.raises(StaleHandle, match="位置偏移"):
        await handle.click()
    assert backend.taps == []


async def test_cancel_window_covers_image_transaction(env):
    """发送阶段检查点在 IMAGE 路径同样生效:采集后取消,不发出点击。"""
    _, _, config, image_target = env
    release = threading.Event()
    entered = threading.Event()

    class SlowCapture(ScriptedBackend):
        def screenshot_png(self):
            entered.set()
            release.wait(2)
            return super().screenshot_png()

    adapter, backend = make(config, SlowCapture([screen_with(PATCH_AT)]))
    task = asyncio.ensure_future(adapter.click(image_target))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task  # 取消已在会话登记
    release.set()
    await asyncio.sleep(0.1)
    assert backend.taps == []  # 取消在发送前生效
