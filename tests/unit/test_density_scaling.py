"""工单 02:P4 密度路径的触发、坐标与旧行为回归(合成帧)。

真机探针闸门未执行(当前无设备,见 docs/validation.md),本文件用合成帧
模拟「采集分辨率相同、屏幕密度不同」:模板取自 440dpi 采集帧,当前帧
内容按 480/440 放大后居中裁回原尺寸。旧单尺度路径必须失配,提供
source_density/current_density 后命中且坐标在 2px 容差内。

合成帧按缩略尺寸构造(320x400):引擎在长边 ≤800 的降采样图上扫描,
缩略尺寸下扫描系数为 1.0,坐标可无换算地对照;真实 1080x2400 的
跨密度真机验收待探针闸门后补。
"""

import json
import pathlib

import cv2
import numpy as np
import pytest

from mobile_auto.imaging.engine import match_template

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "images"

W, H = 320, 400
PATCH_AT = (120, 100)  # (x, y)
SIZE = 40
SRC_DENSITY = 440
CUR_DENSITY = 480
RATIO = CUR_DENSITY / SRC_DENSITY  # 1.0909…


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


def make_patch(seed: int = 7) -> np.ndarray:
    """UI 形态合成模板:平滑灰度纹理 + 纯色色块(与多尺度用例同源思路)。"""
    gray = cv2.GaussianBlur(
        np.random.default_rng(seed).integers(70, 180, size=(SIZE, SIZE), dtype=np.uint8),
        (0, 0),
        1.5,
    )
    patch = np.stack([gray, gray, gray], axis=-1)
    patch[8:20, 8:20] = (40, 120, 220)
    patch[24:34, 24:34] = (220, 60, 60)
    return patch


def make_frame(seed: int = 1) -> np.ndarray:
    frame = np.random.default_rng(seed).integers(
        0, 255, size=(H, W, 3), dtype=np.uint8
    )
    x, y = PATCH_AT
    frame[y : y + SIZE, x : x + SIZE] = make_patch()
    return frame


def density_scaled(frame: np.ndarray, factor: float) -> np.ndarray:
    """整帧按密度比缩放,再居中裁/补回原尺寸(同分辨率不同密度的像素表现)。"""
    scaled = cv2.resize(frame, None, fx=factor, fy=factor, interpolation=cv2.INTER_LINEAR)
    sh, sw = scaled.shape[:2]
    if sh >= H and sw >= W:
        y0, x0 = (sh - H) // 2, (sw - W) // 2
        return np.ascontiguousarray(scaled[y0 : y0 + H, x0 : x0 + W])
    canvas = np.zeros_like(frame)
    y0, x0 = (H - sh) // 2, (W - sw) // 2
    canvas[y0 : y0 + sh, x0 : x0 + sw] = scaled
    return canvas


@pytest.fixture
def cross_density():
    """(当前帧 PNG, 模板 PNG);模板为采集密度下的原始裁片。"""
    frame = make_frame()
    x, y = PATCH_AT
    template = np.ascontiguousarray(frame[y : y + SIZE, x : x + SIZE])
    return png_bytes(density_scaled(frame, RATIO)), png_bytes(template)


def test_density_path_matches_where_single_scale_fails(cross_density):
    current, template = cross_density
    # 旧路径(单尺度):同分辨率不缩放 → 0 命中,这是工单动机
    assert match_template(current, template) == []

    results = match_template(
        current,
        template,
        source_resolution=(W, H),
        source_density=SRC_DENSITY,
        current_density=CUR_DENSITY,
    )
    assert len(results) == 1
    # 变换后真实中心:(138.2, 112.7):相对屏心 (160,200) 的位移按 1.0909 放大
    assert abs(results[0].center[0] - 138) <= 2
    assert abs(results[0].center[1] - 113) <= 2
    # 模板在当前屏上放大 ~9%:宽度 = 40 * 1.0909 ≈ 43.6
    assert abs(results[0].width - round(SIZE * RATIO)) <= 2


def test_density_path_requires_both_densities(cross_density):
    current, template = cross_density
    # 只有采集密度(旧 meta)或只有当前密度 → 不触发,行为同旧路径
    assert match_template(
        current, template, source_resolution=(W, H), source_density=SRC_DENSITY
    ) == []
    assert match_template(
        current, template, source_resolution=(W, H), current_density=CUR_DENSITY
    ) == []


def test_density_path_below_threshold_does_not_trigger(cross_density):
    """相对差 ≤2%(440→448 为 1.8%)视为测量噪声:不缩放,回到旧行为。"""
    current, template = cross_density
    assert match_template(
        current,
        template,
        source_resolution=(W, H),
        source_density=440,
        current_density=448,
    ) == []


def test_same_density_results_identical_to_baseline():
    """同密度 + 同分辨率:提供 density 与不提供的结果逐字段一致。"""
    frame = make_frame()
    x, y = PATCH_AT
    template = png_bytes(np.ascontiguousarray(frame[y : y + SIZE, x : x + SIZE]))
    shot = png_bytes(frame)
    baseline = match_template(shot, template, source_resolution=(W, H))
    with_density = match_template(
        shot,
        template,
        source_resolution=(W, H),
        source_density=SRC_DENSITY,
        current_density=SRC_DENSITY,
    )
    assert with_density == baseline
    assert [(m.x, m.y) for m in baseline] == [PATCH_AT]


def test_resolution_path_ignores_density():
    """分辨率不同:仍走面积比路径,密度参数不改变结果。"""
    frame = make_frame()
    x, y = PATCH_AT
    template = png_bytes(np.ascontiguousarray(frame[y : y + SIZE, x : x + SIZE]))
    big = cv2.resize(frame, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_AREA)
    shot = png_bytes(big)
    baseline = match_template(shot, template, source_resolution=(W, H))
    with_density = match_template(
        shot,
        template,
        source_resolution=(W, H),
        source_density=SRC_DENSITY,
        current_density=CUR_DENSITY,
    )
    assert baseline and with_density == baseline


def _negative_samples():
    samples = json.loads((FIXTURES / "samples.json").read_text(encoding="utf-8"))
    return [s for s in samples if s["expect"] == "empty"]


@pytest.mark.parametrize(
    "sample", _negative_samples(), ids=lambda s: f"{s['template']}@{s['screenshot']}"
)
def test_density_path_keeps_real_negative_samples_empty(sample):
    """密度路径不得放宽阈值,让真实负样本(含同形异色图标)误匹配。"""
    screenshot = (FIXTURES / sample["screenshot"]).read_bytes()
    template = (FIXTURES / sample["template"]).read_bytes()
    results = match_template(
        screenshot,
        template,
        source_resolution=(1080, 2400),
        source_density=SRC_DENSITY,
        current_density=CUR_DENSITY,
    )
    assert results == [], sample.get("note", sample)
