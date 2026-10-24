"""模板匹配引擎:纯函数,可脱离驱动与网页独立运行(plan 6.1)。

算法固定行为:截图与模板解码为一致 8-bit RGB;灰度仅做候选定位
(TM_CCOEFF_NORMED),颜色信息保留用于颜色验证;候选取局部峰值,
同分按 (y,x) 稳定排序;NMS 抑制重叠;结果还原为全截图坐标并按
(y,x) 排序;每个候选再做颜色验证(同色轻微渲染差异接受,
形状相同状态色不同拒绝,决定 Q3)。

校准初值(非已证明最优值;样本集固定后调整须记录依据于本文件):
- GRAY_STDDEV_MIN = 2(0-255 标度):低纹理模板拒绝
- DEFAULT_CONFIDENCE = 0.90:灰度相关性阈值,可被 Target.confidence 覆盖
- NMS_IOU = 0.30:重叠抑制阈值
- MAX_PEAKS = 2000 / MAX_MATCHES = 100:候选与结果上限,超限拒绝不静默丢弃
- COLOR_CONFIDENCE_MIN = 0.90:颜色验证阈值(V2 P3)。候选窗口与模板各
  裁剪到 [10,245] 后转 HSV,H/S/V 三通道分别计算去均值相关,取最小值
  达阈值才通过。由固定正负样本组校准:jpeg85 渲染差异正样本须接受
  (三通道最小值实测 ≥0.97)、通道对调状态色负样本须拒绝(实测 ≤0.0)。
  方差近零的通道对显式定义(见 _channel_ncc),不依赖未定义的 0/0。
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
COLOR_CONFIDENCE_MIN = 0.90  # V2 P3:HSV 三通道最小相关性下限
_COLOR_CLIP = (10, 245)  # 转 HSV 前的 RGB 裁剪范围,压低极端值对色相的噪声
_CHANNEL_FLAT_STD = 1.0  # 通道标准差低于此值视为常量通道(0-255 标度)
_CHANNEL_FLAT_MEAN_DIFF = 2.0  # 双常量通道的均值差容忍(轻微渲染差异)


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


@dataclass(frozen=True)
class PreparedTemplate:
    """一次准备、多次匹配的模板:解码与静态校验(不透明、可解码、灰度
    标准差)在准备阶段完成;匹配阶段不再重复。缓存值类型(P2)。"""

    rgb: np.ndarray
    gray: np.ndarray
    hsv: np.ndarray


def prepare_template(template_png: bytes, *, what: str = "模板") -> PreparedTemplate:
    """解码模板并完成静态校验;低纹理模板在此拒绝(与 v1 相同的判定)。

    HSV 在此一次算好(裁剪到 [10,245] 后转换),供候选颜色验证复用(P3)。
    """
    template = decode_rgb(template_png, what=what)
    template_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    if float(template_gray.std()) < GRAY_STDDEV_MIN:
        raise InvalidTemplate(
            "模板灰度标准差过低(近纯色),相关性会退化;请使用有纹理的模板"
        )
    return PreparedTemplate(
        rgb=template,
        gray=template_gray,
        hsv=cv2.cvtColor(np.clip(template, *_COLOR_CLIP), cv2.COLOR_RGB2HSV),
    )


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
    return _match_prepared(
        screenshot, prepare_template(template_png), threshold, region
    )


def match_prepared(
    screenshot_png: bytes,
    template: PreparedTemplate,
    *,
    confidence: float | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> list[MatchResult]:
    """match_template 的免重复解码入口:模板已通过 prepare_template 校验。"""
    threshold = (
        DEFAULT_CONFIDENCE if confidence is None else _validate_confidence(confidence)
    )
    return _match_prepared(
        decode_rgb(screenshot_png, what="截图"), template, threshold, region
    )


def _match_prepared(
    screenshot: np.ndarray,
    template: PreparedTemplate,
    threshold: float,
    region: tuple[int, int, int, int] | None,
) -> list[MatchResult]:
    rx, ry, rw, rh = _validate_region(region, (screenshot.shape[1], screenshot.shape[0]))

    search = screenshot[ry : ry + rh, rx : rx + rw]
    th, tw = template.rgb.shape[:2]
    if th > rh or tw > rw:
        raise InvalidTemplate(
            f"模板 {tw}x{th} 大于搜索区域 {rw}x{rh},无法匹配"
        )

    search_gray = cv2.cvtColor(search, cv2.COLOR_RGB2GRAY)
    scores = cv2.matchTemplate(search_gray, template.gray, cv2.TM_CCOEFF_NORMED)
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
        if not _color_matches(search, template.hsv, px, py, tw, th):
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
    """提取达阈值的局部峰值(8 邻域极大);向量化实现,行主序即 (y,x) 稳定排序。

    cv2.dilate 的边界外取 -inf,与逐格实现的 -inf 填充语义一致:数组边缘
    的格子只与界内邻居竞争。平台区(多个等分相邻格)全部视为峰值,由
    NMS 去重,与 v1 行为一致。
    """
    above = scores >= threshold
    if not above.any():
        return []
    neighborhood_max = cv2.dilate(scores, np.ones((3, 3), dtype=np.uint8))
    ys, xs = np.nonzero(above & (scores == neighborhood_max))
    return [(int(y), int(x), float(scores[y, x])) for y, x in zip(ys, xs, strict=True)]


def _non_max_suppress(
    peaks: list[tuple[int, int, float]], width: int, height: int
) -> list[tuple[int, int, float]]:
    """NMS:分数降序、同分 (y,x) 顺序处理;IoU 大于阈值时抑制。

    同尺寸框的 IoU 批量向量化计算;处理序与排序键和 v1 完全一致。
    """
    if not peaks:
        return []
    scores = np.array([p[2] for p in peaks])
    ys = np.array([p[0] for p in peaks], dtype=np.int64)
    xs = np.array([p[1] for p in peaks], dtype=np.int64)
    order = np.lexsort((xs, ys, -scores))
    kept: list[tuple[int, int, float]] = []
    kept_x = np.empty(0, dtype=np.int64)
    kept_y = np.empty(0, dtype=np.int64)
    for idx in order:
        px, py, score = int(xs[idx]), int(ys[idx]), float(scores[idx])
        if kept_x.size:
            ix = np.maximum(px, kept_x)
            iy = np.maximum(py, kept_y)
            ix2 = np.minimum(px + width, kept_x + width)
            iy2 = np.minimum(py + height, kept_y + height)
            inter = np.maximum(ix2 - ix, 0) * np.maximum(iy2 - iy, 0)
            union = 2 * width * height - inter
            if (inter > union * NMS_IOU).any():
                continue
        kept.append((py, px, score))
        kept_x = np.append(kept_x, px)
        kept_y = np.append(kept_y, py)
    return kept


def _channel_ncc(a: np.ndarray, b: np.ndarray) -> float:
    """同尺寸单通道的去均值相关(TM_CCOEFF_NORMED 的同尺寸特例)。

    常量通道对显式定义,不依赖未定义的 0/0:双方常量且均值差在容忍内
    视为完全一致(纯色图标的 S/H 通道常见),常量值不同或仅一方常量
    视为不相关。非 RGB 的 HSV 色相为 0-180 标度,不做环形处理(与
    借鉴来源 Airtest cal_rgb_confidence 相同的已知限制)。
    """
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    da = a - a.mean()
    db = b - b.mean()
    na = float(np.sqrt((da * da).sum()))
    nb = float(np.sqrt((db * db).sum()))
    a_flat = na / math.sqrt(da.size) < _CHANNEL_FLAT_STD  # L2 范数换算逐像素 std
    b_flat = nb / math.sqrt(db.size) < _CHANNEL_FLAT_STD
    if a_flat and b_flat:
        return 1.0 if abs(float(a.mean()) - float(b.mean())) <= _CHANNEL_FLAT_MEAN_DIFF else 0.0
    if a_flat or b_flat:
        return 0.0
    return float(np.clip((da * db).sum() / (na * nb), -1.0, 1.0))


def _color_matches(
    search: np.ndarray, template_hsv: np.ndarray, px: int, py: int, tw: int, th: int
) -> bool:
    """颜色验证(P3):候选窗口与模板各自裁剪转 HSV,H/S/V 三通道
    去均值相关取最小值,达 COLOR_CONFIDENCE_MIN 才通过。

    同色轻微渲染差异三通道仍高度相关,接受;形状相同、状态色不同
    (如通道对调)在色相/饱和度通道上失去相关,拒绝(决定 Q3)。
    """
    candidate = search[py : py + th, px : px + tw]
    candidate_hsv = cv2.cvtColor(
        np.clip(candidate, *_COLOR_CLIP), cv2.COLOR_RGB2HSV
    )
    return min(
        _channel_ncc(candidate_hsv[:, :, i], template_hsv[:, :, i]) for i in range(3)
    ) >= COLOR_CONFIDENCE_MIN
