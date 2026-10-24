"""P4a 有界多尺度:采集分辨率≠当前截图时自动适配,同分辨率行为不变。"""

import cv2
import numpy as np

from mobile_auto.imaging.engine import match_template


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


def make_screen(seed: int = 1, w: int = 200, h: int = 150) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def make_patch(seed: int = 7, size: int = 30) -> np.ndarray:
    """UI 形态的合成模板:平滑灰度纹理 + 纯色色块。

    纯随机 RGB 噪声(含模糊后)的色相逐像素随机,对跨尺度重采样天然
    敌对,不代表真实 UI(大面积纯色 + 抗锯齿边缘,实测跨分辨率 H≥0.93);
    跨分辨率验收以 tests/unit/test_image_samples.py 的真实样本为准,
    本文件只验证几何与尺度语义。
    """
    gray = cv2.GaussianBlur(
        np.random.default_rng(seed).integers(70, 180, size=(size, size), dtype=np.uint8),
        (0, 0),
        1.5,
    )
    patch = np.stack([gray, gray, gray], axis=-1)
    patch[6:16, 6:16] = (40, 120, 220)  # 纯蓝色块
    patch[18:26, 18:26] = (220, 60, 60)  # 纯红色块
    return patch


def resized(a: np.ndarray, factor: float) -> np.ndarray:
    return cv2.resize(a, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)


def test_cross_resolution_match_adapts_scale():
    """截图 1.25×(模拟高 DPR 设备)+ 原尺寸模板:v1 单尺度必然 0 命中,
    提供采集分辨率后应命中,中心偏差 ≤2 像素、尺度还原 ±10%。"""
    screen = make_screen()
    patch = make_patch()
    screen[50:80, 100:130] = patch
    big = resized(screen, 1.25)

    assert match_template(png_bytes(big), png_bytes(patch)) == []  # 单尺度:跨分辨率失效
    results = match_template(
        png_bytes(big), png_bytes(patch), source_resolution=(200, 150)
    )
    assert len(results) == 1
    # 真实中心:(100+15)*1.25=143.75,(50+15)*1.25=81.25
    assert abs(results[0].center[0] - 144) <= 2
    assert abs(results[0].center[1] - 81) <= 2
    assert abs(results[0].width / 30 - 1.25) <= 0.1  # 尺度还原


def test_cross_resolution_downscaled_screen():
    """截图 0.85×(模板相对变大)同样适配。"""
    screen = make_screen()
    patch = make_patch()
    screen[50:80, 100:130] = patch
    small = resized(screen, 0.85)
    results = match_template(
        png_bytes(small), png_bytes(patch), source_resolution=(200, 150)
    )
    assert len(results) == 1
    # 真实中心:(115*0.85, 65*0.85)=(97.75, 55.25)
    assert abs(results[0].center[0] - 98) <= 2
    assert abs(results[0].center[1] - 55) <= 2


def test_same_resolution_ignores_multiscale_path():
    """采集分辨率与截图一致:多尺度路径不启用,行为与 v1 相同。"""
    screen = make_screen()
    patch = make_patch()
    screen[50:80, 100:130] = patch
    results = match_template(
        png_bytes(screen), png_bytes(patch), source_resolution=(200, 150)
    )
    assert [(m.x, m.y) for m in results] == [(100, 50)]
    assert results[0].width == 30 and results[0].height == 30


def test_cross_resolution_without_target_is_empty():
    """目标不在画面:跨分辨率路径同样返回空列表,不报错。"""
    other = make_screen(seed=42)
    results = match_template(
        png_bytes(other), png_bytes(make_patch()), source_resolution=(200, 150)
    )
    assert results == []


def test_multiscale_respects_region():
    """region 语义在多尺度路径下不变:区域内无目标返回空。"""
    screen = make_screen()
    patch = make_patch()
    screen[50:80, 100:130] = patch
    big = resized(screen, 1.25)
    empty = match_template(
        png_bytes(big), png_bytes(patch), region=(0, 0, 100, 100),
        source_resolution=(200, 150),
    )
    assert empty == []
    hit = match_template(
        png_bytes(big), png_bytes(patch), region=(100, 50, 80, 50),
        source_resolution=(200, 150),
    )
    assert len(hit) == 1


def test_multiscale_finds_all_instances_at_dominant_scale():
    """多尺度路径在全区域按最优尺度收尾:同尺度的多个实例全部返回。"""
    screen = make_screen()
    patch = make_patch()
    screen[50:80, 100:130] = patch
    screen[50:80, 30:60] = patch
    big = resized(screen, 1.25)
    results = match_template(
        png_bytes(big), png_bytes(patch), source_resolution=(200, 150)
    )
    assert sorted((m.x, m.y) for m in results) == [(38, 63), (125, 63)]
