"""P4b 采集位置先验:capture_center 预测区域优先搜索,未命中回退全区域。

几何语义以合成样本验证;行为不变量:
- 命中预测区域 → 只返回预测区域内的实例(多实例语义收窄);
- 预测区域未命中 → 回退全区域,"找不到就是找不到";
- 显式 region 优先于先验,region 内未命中不回退。
"""

import cv2
import numpy as np
import pytest

from mobile_auto import InvalidTemplate
from mobile_auto.imaging.engine import (
    capture_center_from_region,
    match_template,
    validate_capture_center,
)


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


def make_screen(seed: int = 1, w: int = 400, h: int = 300) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def make_patch(seed: int = 7, size: int = 30) -> np.ndarray:
    """UI 形态的合成模板(平滑纹理 + 色块),与 test_image_multiscale 同源。"""
    gray = cv2.GaussianBlur(
        np.random.default_rng(seed).integers(70, 180, size=(size, size), dtype=np.uint8),
        (0, 0),
        1.5,
    )
    patch = np.stack([gray, gray, gray], axis=-1)
    patch[6:16, 6:16] = (40, 120, 220)
    patch[18:26, 18:26] = (220, 60, 60)
    return patch


def test_prior_hit_returns_only_predicted_instance():
    """预测区域内命中:只返回预测区域内的实例,远处同图实例不返回。"""
    screen = make_screen()
    patch = make_patch()
    screen[120:150, 250:280] = patch  # 预测中心附近(屏幕中心 200,150)
    screen[20:50, 20:50] = patch  # 远角同图实例
    region = (250, 120, 30, 30)
    center = capture_center_from_region(region, (400, 300))

    without = match_template(png_bytes(screen), png_bytes(patch))
    assert sorted((m.x, m.y) for m in without) == [(20, 20), (250, 120)]

    with_prior = match_template(
        png_bytes(screen), png_bytes(patch), capture_center=center
    )
    assert [(m.x, m.y) for m in with_prior] == [(250, 120)]


def test_prior_miss_falls_back_to_full_region():
    """目标只在预测区域外:回退全区域仍命中,找不到就是找不到。"""
    screen = make_screen()
    patch = make_patch()
    screen[20:50, 20:50] = patch  # 远角,远离预测中心
    center = capture_center_from_region((200, 150, 30, 30), (400, 300))
    results = match_template(png_bytes(screen), png_bytes(patch), capture_center=center)
    assert [(m.x, m.y) for m in results] == [(20, 20)]


def test_prior_center_math_roundtrip():
    """capture_center_from_region 与预测换算互逆:中心区域 → (0, 0) 偏移。"""
    assert capture_center_from_region((185, 135, 30, 30), (400, 300)) == (0.0, 0.0)
    nx, ny = capture_center_from_region((250, 120, 30, 30), (400, 300))
    # 中心 (265, 135):nx=(265-200)/300, ny=(135-150)/300
    assert abs(nx - 65 / 300) < 1e-6
    assert abs(ny + 15 / 300) < 1e-6


def test_prior_screen_edge_clipped_region_still_finds():
    """预测中心贴近屏幕边缘,预测区域被裁剪:先验仍生效(命中即返回)。"""
    screen = make_screen(w=400, h=300)
    patch = make_patch()
    screen[5:35, 5:35] = patch  # 左上角
    center = capture_center_from_region((5, 5, 30, 30), (400, 300))
    results = match_template(png_bytes(screen), png_bytes(patch), capture_center=center)
    assert [(m.x, m.y) for m in results] == [(5, 5)]


def test_cross_resolution_prior_uses_scaled_margin():
    """跨分辨率:先验边距按期望尺度放大,预测点按归一化偏移映射到新屏。"""
    screen = make_screen()
    patch = make_patch()
    screen[120:150, 250:280] = patch
    big = cv2.resize(screen, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_AREA)
    center = capture_center_from_region((250, 120, 30, 30), (400, 300))
    results = match_template(
        png_bytes(big),
        png_bytes(patch),
        source_resolution=(400, 300),
        capture_center=center,
    )
    assert len(results) == 1
    assert abs(results[0].center[0] - 265 * 1.25) <= 2
    assert abs(results[0].center[1] - 135 * 1.25) <= 2


def test_explicit_region_wins_and_does_not_fallback():
    """显式 region 优先于先验:region 内未命中返回空,不回退全区域。"""
    screen = make_screen()
    patch = make_patch()
    screen[120:150, 250:280] = patch
    center = capture_center_from_region((250, 120, 30, 30), (400, 300))
    results = match_template(
        png_bytes(screen),
        png_bytes(patch),
        region=(0, 0, 100, 100),
        capture_center=center,
    )
    assert results == []
    hit = match_template(
        png_bytes(screen),
        png_bytes(patch),
        region=(250, 120, 30, 30),
        capture_center=center,
    )
    assert [(m.x, m.y) for m in hit] == [(250, 120)]


def test_capture_center_none_keeps_v1_behavior():
    """不提供 capture_center:全区域搜索,行为与 v1 完全一致。"""
    screen = make_screen()
    patch = make_patch()
    screen[120:150, 250:280] = patch
    screen[20:50, 20:50] = patch
    results = match_template(png_bytes(screen), png_bytes(patch), capture_center=None)
    assert sorted((m.x, m.y) for m in results) == [(20, 20), (250, 120)]


@pytest.mark.parametrize(
    "bad", ["x", 5, [1, 2, 3], [1], (0.1,), [float("nan"), 0], ["a", "b"], [True, 0]]
)
def test_invalid_capture_center_rejected(bad):
    screen = make_screen()
    patch = make_patch()
    with pytest.raises(InvalidTemplate, match="capture_center"):
        match_template(png_bytes(screen), png_bytes(patch), capture_center=bad)
    assert validate_capture_center(None) is None


def test_validate_capture_center_accepts_pairs():
    assert validate_capture_center((0.1, -0.2)) == (0.1, -0.2)
    assert validate_capture_center([0, 0]) == (0.0, 0.0)
