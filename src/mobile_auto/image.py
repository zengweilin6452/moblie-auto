"""模板匹配引擎:纯函数,可脱离驱动与网页独立运行(plan 6.1)。

算法固定行为:截图与模板解码为一致 8-bit RGB;灰度仅做候选定位
(TM_CCOEFF_NORMED),颜色信息保留用于颜色验证;候选取局部峰值,
同分按 (y,x) 稳定排序;NMS 抑制重叠;结果还原为全截图坐标并按
(y,x) 排序;每个候选再做颜色差异验证(同色轻微渲染差异接受,
形状相同状态色不同拒绝,决定 Q3)。

校准初值(非已证明最优值;样本集固定后调整须记录依据于本文件):
- GRAY_STDDEV_MIN = 2(0-255 标度):低纹理模板拒绝
- DEFAULT_CONFIDENCE = 0.90:灰度相关性阈值,可被 Target.confidence 覆盖
- NMS_IOU = 0.30:重叠抑制阈值
- MAX_PEAKS = 2000 / MAX_MATCHES = 100:候选与结果上限,超限拒绝不静默丢弃
- COLOR_DIFF_MAX = 16(0-255 标度,RGB 均值绝对差):颜色验证阈值,
  由固定正负样本组校准(同色轻微渲染差异须接受、状态色差异须拒绝)
- 坐标容差 2 像素:合成样本验收坐标的中心容差
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .errors import InvalidTemplate, MatchOverflow

GRAY_STDDEV_MIN = 2.0
DEFAULT_CONFIDENCE = 0.90
NMS_IOU = 0.30
MAX_PEAKS = 2000
MAX_MATCHES = 100
COLOR_DIFF_MAX = 16.0


@dataclass(frozen=True)
class MatchResult:
    """匹配结果:全截图坐标与分数;不代表业务对象身份,也不代表操作成功。

    center 以边界中心定义。
    """

    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def decode_rgb(png: bytes, *, what: str) -> np.ndarray:
    """PNG 解码为 8-bit RGB;不透明模板要求(含透明蒙版拒绝)。"""
    decoded = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.size == 0:
        raise InvalidTemplate(f"{what}不是可解码的 PNG")
    if decoded.ndim == 3 and decoded.shape[2] == 4:
        alpha = decoded[:, :, 3]
        if (alpha < 255).any():
            raise InvalidTemplate(
                f"{what}含透明像素:首版模板必须为不透明 PNG,拒绝透明蒙版"
            )
        decoded = decoded[:, :, :3]
    elif decoded.ndim == 2:
        decoded = cv2.cvtColor(decoded, cv2.COLOR_GRAY2RGB)
    if decoded.ndim != 3 or decoded.shape[2] != 3:
        raise InvalidTemplate(f"{what}通道数不受支持")
    return np.ascontiguousarray(decoded[:, :, ::-1])  # BGR → RGB


def _validate_region(
    region: tuple[int, int, int, int] | None, size: tuple[int, int]
) -> tuple[int, int, int, int]:
    width, height = size
    if region is None:
        return 0, 0, width, height
    x, y, w, h = region
    if (
        any(isinstance(v, bool) or not isinstance(v, int) for v in region)
        or w <= 0
        or h <= 0
        or x < 0
        or y < 0
        or x + w > width
        or y + h > height
    ):
        raise InvalidTemplate(
            f"搜索区域 {region} 必须为四个整数且完全落在 {width}x{height} 截图内,"
            "不静默裁切"
        )
    return x, y, w, h


def _validate_confidence(confidence: float) -> float:
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 < confidence <= 1
    ):
        raise InvalidTemplate(f"confidence 必须是 (0, 1] 区间内的有限数值:{confidence!r}")
    return float(confidence)


def match_template(
    screenshot_png: bytes,
    template_png: bytes,
    *,
    confidence: float | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> list[MatchResult]:
    """在截图(或其搜索区域)内匹配模板,返回全截图坐标的匹配结果。

    无匹配返回空列表——这是查询结果,不是错误;非法输入(损坏 PNG、
    透明蒙版、低纹理模板、越界区域、超限溢出)显式拒绝。
    """
    threshold = (
        DEFAULT_CONFIDENCE if confidence is None else _validate_confidence(confidence)
    )
    screenshot = decode_rgb(screenshot_png, what="截图")
    template = decode_rgb(template_png, what="模板")
    rx, ry, rw, rh = _validate_region(region, (screenshot.shape[1], screenshot.shape[0]))

    search = screenshot[ry : ry + rh, rx : rx + rw]
    th, tw = template.shape[:2]
    if th > rh or tw > rw:
        raise InvalidTemplate(
            f"模板 {tw}x{th} 大于搜索区域 {rw}x{rh},无法匹配"
        )

    search_gray = cv2.cvtColor(search, cv2.COLOR_RGB2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    if float(template_gray.std()) < GRAY_STDDEV_MIN:
        raise InvalidTemplate(
            "模板灰度标准差过低(近纯色),相关性会退化;请使用有纹理的模板"
        )

    scores = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    if not np.isfinite(scores).all():
        raise InvalidTemplate("匹配产生非有限分数;请检查模板与截图内容")

    peaks = _local_peaks(scores, threshold)
    if len(peaks) > MAX_PEAKS:
        raise MatchOverflow(
            f"候选峰值 {len(peaks)} 超过上限 {MAX_PEAKS};请收缩搜索区域或提高阈值"
        )
    matches = _non_max_suppress(peaks, tw, th)
    if len(matches) > MAX_MATCHES:
        raise MatchOverflow(
            f"最终匹配 {len(matches)} 超过上限 {MAX_MATCHES};不能静默丢弃改变序号"
        )

    results = []
    for py, px, score in matches:
        if not _color_matches(search, template, px, py, tw, th):
            continue  # 形状相同、状态色不同:判为不匹配(决定 Q3)
        results.append(
            MatchResult(
                x=rx + px,
                y=ry + py,
                width=tw,
                height=th,
                confidence=round(float(score), 6),
            )
        )
    results.sort(key=lambda m: (m.y, m.x))
    return results


def _local_peaks(scores: np.ndarray, threshold: float) -> list[tuple[int, int, float]]:
    """提取达阈值的局部峰值(8 邻域极大);同分由调用方按 (y,x) 稳定排序。"""
    padded = np.full(
        (scores.shape[0] + 2, scores.shape[1] + 2), -np.inf, dtype=scores.dtype
    )
    padded[1:-1, 1:-1] = scores
    peaks = []
    rows, cols = scores.shape
    for y in range(rows):
        for x in range(cols):
            score = scores[y, x]
            if score < threshold:
                continue
            neighborhood = padded[y : y + 3, x : x + 3]
            if score >= neighborhood.max():
                peaks.append((y, x, float(score)))
    return peaks


def _non_max_suppress(
    peaks: list[tuple[int, int, float]], width: int, height: int
) -> list[tuple[int, int, float]]:
    """NMS:分数降序、同分 (y,x) 顺序处理;IoU 大于阈值时抑制。"""
    ordered = sorted(peaks, key=lambda p: (-p[2], p[0], p[1]))
    kept: list[tuple[int, int, float]] = []
    for py, px, score in ordered:
        duplicate = False
        for ky, kx, _ in kept:
            iou = _iou(px, py, width, height, kx, ky, width, height)
            if iou > NMS_IOU:
                duplicate = True
                break
        if not duplicate:
            kept.append((py, px, score))
    return kept


def _iou(
    ax: int, ay: int, aw: int, ah: int, bx: int, by: int, bw: int, bh: int
) -> float:
    ix = max(ax, bx)
    iy = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    inter = max(0, ix2 - ix) * max(0, iy2 - iy)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _color_matches(
    search: np.ndarray, template: np.ndarray, px: int, py: int, tw: int, th: int
) -> bool:
    """颜色验证:候选区域与模板的 RGB 均值绝对差不超过阈值。

    同色轻微渲染差异接受;形状相同、状态色不同拒绝(决定 Q3)。
    """
    candidate = search[py : py + th, px : px + tw].astype(np.int16)
    reference = template.astype(np.int16)
    diff = float(np.abs(candidate - reference).mean())
    return diff <= COLOR_DIFF_MAX
