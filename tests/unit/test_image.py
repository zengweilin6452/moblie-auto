"""图像匹配引擎:固定合成样本的精确坐标、排序、颜色验证与拒绝路径。"""

import cv2
import numpy as np
import pytest

from mobile_auto import InvalidTemplate, MatchOverflow
from mobile_auto import image as image_module
from mobile_auto.image import MatchResult, decode_rgb, match_template


def rng(seed: int):
    return np.random.default_rng(seed)


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


SCREEN_W, SCREEN_H, PATCH = 200, 150, 20
PATCH_AT = (100, 50)  # (x, y)


def make_background() -> np.ndarray:
    return rng(1).integers(0, 255, size=(SCREEN_H, SCREEN_W, 3), dtype=np.uint8)


def make_patch(seed: int = 7) -> np.ndarray:
    return rng(seed).integers(0, 255, size=(PATCH, PATCH, 3), dtype=np.uint8)


@pytest.fixture
def screenshot_png():
    screen = make_background()
    px, py = PATCH_AT
    screen[py : py + PATCH, px : px + PATCH] = make_patch()
    return png_bytes(screen)


@pytest.fixture
def template_png():
    return png_bytes(make_patch())


def test_exact_match_coordinates_and_center(screenshot_png, template_png):
    results = match_template(screenshot_png, template_png)
    assert results == [
        MatchResult(x=100, y=50, width=20, height=20, confidence=results[0].confidence)
    ]
    assert results[0].confidence > 0.99
    assert results[0].center == (110, 60)  # 边界中心,容差 2 像素内
    assert abs(results[0].center[0] - 110) <= 2 and abs(results[0].center[1] - 60) <= 2


def test_results_sorted_by_y_then_x(screenshot_png, template_png):
    screen = make_background()
    patch = make_patch()
    screen[80 : 80 + PATCH, 30 : 30 + PATCH] = patch
    screen[50 : 50 + PATCH, 100 : 100 + PATCH] = patch
    results = match_template(png_bytes(screen), png_bytes(patch))
    # 按 (y, x) 升序:y=50 的 (100,50) 在 y=80 的 (30,80) 之前
    assert [(m.x, m.y) for m in results] == [(100, 50), (30, 80)]


def test_region_offset_restored_to_full_screenshot(screenshot_png, template_png):
    results = match_template(screenshot_png, template_png, region=(90, 40, 60, 60))
    assert [(m.x, m.y) for m in results] == [(100, 50)]
    empty = match_template(screenshot_png, template_png, region=(0, 0, 50, 50))
    assert empty == []  # 无结果是查询结果,不是错误


def test_render_variation_accepted_within_color_threshold(screenshot_png, template_png):
    screen = make_background()
    px, py = PATCH_AT
    screen[py : py + PATCH, px : px + PATCH] = make_patch()
    noisy = np.clip(
        make_patch().astype(np.int16) + rng(9).integers(-3, 4, size=(PATCH, PATCH, 3)),
        0,
        255,
    ).astype(np.uint8)
    screen[10 : 10 + PATCH, 10 : 10 + PATCH] = noisy  # 同色轻微渲染差异
    results = match_template(png_bytes(screen), template_png)
    assert sorted((m.x, m.y) for m in results) == [(10, 10), (100, 50)]


def test_state_color_change_rejected(screenshot_png, template_png):
    screen = make_background()
    px, py = PATCH_AT
    screen[py : py + PATCH, px : px + PATCH] = make_patch()
    recolored = make_patch()[:, :, ::-1]  # 形状相同、通道对调=状态色不同
    screen[10 : 10 + PATCH, 10 : 10 + PATCH] = recolored
    results = match_template(png_bytes(screen), template_png)
    assert [(m.x, m.y) for m in results] == [(100, 50)]  # 变色副本不出现


def test_local_color_dot_state_change_rejected():
    """局部色点状态差异:红/绿点亮度差大,灰度相关 0.764 被灰度阈值
    拒绝(颜色门的均值差 3.33 < 16 本身拦不住;此用例钉住灰度阶段的
    拦截行为,P3 校准记录)。"""
    patch = rng(3).integers(100, 160, size=(24, 24, 3), dtype=np.uint8)
    patch[6:10, 6:10] = (220, 40, 40)  # 红点
    green = patch.copy()
    green[6:10, 6:10] = (40, 220, 40)  # 状态色不同的副本
    screen = make_background()
    screen[20 : 44, 20 : 44] = green
    screen[100 : 124, 100 : 124] = patch
    results = match_template(png_bytes(screen), png_bytes(patch))
    assert [(m.x, m.y) for m in results] == [(100, 100)]


def test_uniform_dimming_rejected():
    """整体变暗 30% 的禁用态:灰度 CCOEFF 对线性偏移不敏感(≈1.0 放行)、
    H/S/V 相关同样≈1.0,由承袭 v1 的均值差门(diff≈39>16)拒绝(P3)。"""
    import cv2

    base = cv2.GaussianBlur(
        rng(11).integers(60, 200, size=(24, 24, 3), dtype=np.uint8), (0, 0), 1.5
    )
    dimmed = (base * 0.7).astype(np.uint8)
    screen = make_background()
    screen[20 : 44, 20 : 44] = dimmed
    screen[100 : 124, 100 : 124] = base
    results = match_template(png_bytes(screen), png_bytes(base))
    assert [(m.x, m.y) for m in results] == [(100, 100)]


def test_low_texture_template_rejected():
    solid = np.full((20, 20, 3), 128, dtype=np.uint8)
    with pytest.raises(InvalidTemplate, match="标准差过低"):
        match_template(png_bytes(make_background()), png_bytes(solid))


def test_transparent_template_rejected():
    rgba = np.dstack([make_patch(), np.full((PATCH, PATCH), 128, dtype=np.uint8)])
    ok, buf = cv2.imencode(".png", rgba[:, :, ::-1])
    assert ok
    with pytest.raises(InvalidTemplate, match="不透明"):
        match_template(png_bytes(make_background()), buf.tobytes())


def test_corrupt_png_rejected():
    with pytest.raises(InvalidTemplate, match="可解码"):
        match_template(png_bytes(make_background()), b"not-a-png")


def test_region_out_of_bounds_rejected_not_clipped(screenshot_png, template_png):
    with pytest.raises(InvalidTemplate, match="完全落在"):
        match_template(screenshot_png, template_png, region=(150, 140, 60, 60))
    with pytest.raises(InvalidTemplate, match="完全落在"):
        match_template(screenshot_png, template_png, region=(10, 10, 0, 10))


def test_template_larger_than_region_rejected(screenshot_png, template_png):
    with pytest.raises(InvalidTemplate, match="大于搜索区域"):
        match_template(screenshot_png, template_png, region=(0, 0, 15, 15))


@pytest.mark.parametrize(("value",), [(0,), (1.5,), (float("nan"),), (True,)])
def test_invalid_confidence_rejected(screenshot_png, template_png, value):
    with pytest.raises(InvalidTemplate, match="confidence"):
        match_template(screenshot_png, template_png, confidence=value)


def test_peak_overflow_rejected(screenshot_png, template_png, monkeypatch):
    screen = make_background()
    patch = make_patch()
    screen[10 : 10 + PATCH, 10 : 10 + PATCH] = patch
    screen[50 : 50 + PATCH, 100 : 100 + PATCH] = patch
    monkeypatch.setattr(image_module, "MAX_PEAKS", 1)  # 两个副本产生 ≥2 峰值
    with pytest.raises(MatchOverflow, match="候选峰值"):
        match_template(png_bytes(screen), png_bytes(patch))


def test_match_overflow_after_nms_rejected(monkeypatch):
    screen = make_background()
    patch = make_patch()
    screen[10 : 10 + PATCH, 10 : 10 + PATCH] = patch
    screen[100 : 100 + PATCH, 150 : 150 + PATCH] = patch
    monkeypatch.setattr(image_module, "MAX_MATCHES", 1)
    with pytest.raises(MatchOverflow, match="最终匹配"):
        match_template(png_bytes(screen), png_bytes(patch))


def test_decode_rgb_returns_original_rgb():
    original = make_patch()
    assert np.array_equal(decode_rgb(png_bytes(original), what="模板"), original)
