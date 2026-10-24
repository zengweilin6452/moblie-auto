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
- 颜色验证为三段门(V2 P3,候选窗口与模板各裁剪到 [10,245] 后转 HSV):
  H/S 通道去均值相关 ≥ COLOR_CONFIDENCE_MIN=0.85(色相状态判别主通道),
  V 通道 ≥ COLOR_VALUE_CONFIDENCE_MIN=0.70(抗跨分辨率重采样相位差),
  RGB 逐像素均值绝对差 ≤ COLOR_MEAN_DIFF_MAX=16(承袭 v1,拦均匀变暗/
  去饱和的禁用态——灰度 CCOEFF 与去均值相关都对线性偏移不敏感,
  单独用相关性会放过)。2026-10-24 实测,各负样本由不同门拦截:
  通道对调(亮度保持)灰度分 0.997 放行、真实样本 t-icon-recolor
  H=0.230 / 合成样本 H=0.348 拒绝(H/S 门职责);局部色点与大色块
  状态色(亮度改变)灰度分 0.764 / -0.221,由灰度阈值拒绝;均匀变暗
  30% 灰度分≈1.0、H/S/V≈1.0,由均值差 39.3 拒绝。正样本下限:8 个
  真实正样本(含两组 jpeg85)H/S/V=1.000;跨分辨率正样本(81px 彩色
  图标 1.25×)H=0.933/S=0.985/V=0.776,合成 UI 形态样本 0.85× 时
  H=0.889(半像素边缘错位的分辨率极限)——H/S 取 0.85、V 取 0.70
  覆盖正样本下限,与负样本上限 0.348 之间余量 ≥0.5。
  方差近零的通道对显式定义(见 _channel_ncc),不依赖未定义的 0/0。
- 坐标容差 2 像素:合成样本验收坐标的中心容差
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from ..errors import InvalidTemplate, MatchOverflow

GRAY_STDDEV_MIN = 2.0
DEFAULT_CONFIDENCE = 0.90
NMS_IOU = 0.30
MAX_PEAKS = 2000
MAX_MATCHES = 100
COLOR_CONFIDENCE_MIN = 0.85  # V2 P3:色相/饱和度通道的最小相关下限
COLOR_VALUE_CONFIDENCE_MIN = 0.70  # 明度通道的最小相关下限(放宽:抗重采样相位差)
COLOR_MEAN_DIFF_MAX = 16.0  # RGB 均值绝对差上限(承袭 v1:拦均匀变暗/变灰状态)
_COLOR_CLIP = (10, 245)  # 转 HSV 前的 RGB 裁剪范围,压低极端值对色相的噪声
_CHANNEL_FLAT_STD = 1.0  # 通道标准差低于此值视为常量通道(0-255 标度)
_CHANNEL_FLAT_MEAN_DIFF = 2.0  # 双常量通道的均值差容忍(轻微渲染差异)

# V2 P4a 有界多尺度(采集分辨率≠当前截图时自动启用;初值见 docs/image-v2-plan.md)
_SCALE_SPAN = 1.3  # 期望比例两侧的搜索倍率
_SCALE_COARSE_STEP = 0.05  # 粗扫步长
_SCALE_FINE_STEP = 0.01  # 细扫步长(粗扫最优邻域)
_SCALE_SEARCH_LONG_EDGE = 800  # 粗细扫在长边不超过此值的降采样图上进行
_SCALE_TEMPLATE_MIN = 10  # 缩放后模板任一边低于此像素则跳过该尺度


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
    source_resolution: tuple[int, int] | None = None,
) -> list[MatchResult]:
    """在截图(或其搜索区域)内匹配模板,返回全截图坐标的匹配结果。

    无匹配返回空列表——这是查询结果,不是错误;非法输入(损坏 PNG、
    透明蒙版、低纹理模板、越界区域、超限溢出)显式拒绝。

    source_resolution 为模板采集时的屏幕分辨率(P4a):与当前截图不一致
    时自动启用有界多尺度搜索;一致或缺省为单尺度,行为与 v1 相同。
    """
    threshold = (
        DEFAULT_CONFIDENCE if confidence is None else _validate_confidence(confidence)
    )
    screenshot = decode_rgb(screenshot_png, what="截图")
    return _match_prepared(
        screenshot,
        prepare_template(template_png),
        threshold,
        region,
        source_resolution,
    )


def match_prepared(
    screenshot_png: bytes,
    template: PreparedTemplate,
    *,
    confidence: float | None = None,
    region: tuple[int, int, int, int] | None = None,
    source_resolution: tuple[int, int] | None = None,
) -> list[MatchResult]:
    """match_template 的免重复解码入口:模板已通过 prepare_template 校验。"""
    threshold = (
        DEFAULT_CONFIDENCE if confidence is None else _validate_confidence(confidence)
    )
    return _match_prepared(
        decode_rgb(screenshot_png, what="截图"),
        template,
        threshold,
        region,
        source_resolution,
    )


def _match_prepared(
    screenshot: np.ndarray,
    template: PreparedTemplate,
    threshold: float,
    region: tuple[int, int, int, int] | None,
    source_resolution: tuple[int, int] | None = None,
) -> list[MatchResult]:
    rx, ry, rw, rh = _validate_region(region, (screenshot.shape[1], screenshot.shape[0]))
    search = screenshot[ry : ry + rh, rx : rx + rw]

    if source_resolution is not None:
        screen_size = (screenshot.shape[1], screenshot.shape[0])
        if (int(source_resolution[0]), int(source_resolution[1])) != screen_size:
            template = _adapt_scale(
                cv2.cvtColor(search, cv2.COLOR_RGB2GRAY),
                template,
                screen_size,
                source_resolution,
            )
            if template is None:
                return []  # 所有尺度下模板都无法放进搜索区域

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
        candidate = search[py : py + th, px : px + tw]
        candidate_hsv = cv2.cvtColor(
            np.clip(candidate, *_COLOR_CLIP), cv2.COLOR_RGB2HSV
        )
        if not _color_matches(template, candidate, candidate_hsv):
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


def _scales_between(lo: float, hi: float, step: float) -> list[float]:
    """确定性尺度序列:[lo, hi] 闭区间按 step 均分,避免浮点累加漂移。"""
    count = int(round((hi - lo) / step))
    return [round(lo + i * step, 10) for i in range(count + 1)]


def _expected_scale(screen_size: tuple[int, int], source: tuple[int, int]) -> float:
    """期望均匀缩放比例:屏幕面积比的平方根(宽高比不同时取几何折中)。"""
    return math.sqrt((screen_size[0] * screen_size[1]) / (source[0] * source[1]))


def _scan_scales(
    search_gray: np.ndarray,
    template_gray: np.ndarray,
    scales: list[float],
) -> float | None:
    """在(降采样的)灰度搜索图上逐尺度匹配,返回最优尺度。

    平坦区域可能产生非有限分数,粗扫阶段按 -1 处理(收尾阶段仍在全
    分辨率上做严格的有限性校验与阈值判定)。
    """
    best_scale = None
    best_score = -np.inf
    for scale in scales:
        tw = round(template_gray.shape[1] * scale)
        th = round(template_gray.shape[0] * scale)
        if (
            min(tw, th) < _SCALE_TEMPLATE_MIN
            or tw > search_gray.shape[1]
            or th > search_gray.shape[0]
        ):
            continue
        resized = cv2.resize(
            template_gray,
            (tw, th),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        )
        scores = cv2.matchTemplate(search_gray, resized, cv2.TM_CCOEFF_NORMED)
        scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
        score = float(scores.max())
        if score > best_score:
            best_score, best_scale = score, scale
    return best_scale


def _adapt_scale(
    search_gray: np.ndarray,
    template: PreparedTemplate,
    screen_size: tuple[int, int],
    source_resolution: tuple[int, int],
) -> PreparedTemplate | None:
    """有界多尺度(P4a):粗扫+细扫估计最优尺度,返回按该尺度缩放的新模板。

    粗细扫都在长边 ≤ _SCALE_SEARCH_LONG_EDGE 的降采样搜索图上进行
    (借鉴 Airtest mstpl 的 src_max 降本):扫描前把全分辨率尺度乘上
    降采样系数,扫描结果再除回,保持尺度体系一致。无可用尺度(模板
    缩放后过小或放不进搜索区域)返回 None,语义为无匹配。期望比例
    s0 下模板大于搜索区域属使用错误,返回原尺寸模板,交给尺寸校验拒绝。
    """
    s0 = _expected_scale(screen_size, source_resolution)
    lo, hi = s0 / _SCALE_SPAN, s0 * _SCALE_SPAN
    th, tw = template.rgb.shape[:2]
    search_w, search_h = search_gray.shape[1], search_gray.shape[0]
    if round(tw * s0) > search_w or round(th * s0) > search_h:
        return template  # 保持原尺寸,交给尺寸校验报"大于搜索区域"

    downscale = min(1.0, _SCALE_SEARCH_LONG_EDGE / max(search_w, search_h))
    small = (
        cv2.resize(search_gray, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA)
        if downscale < 1.0
        else search_gray
    )

    def scan(full_scales: list[float]) -> float | None:
        small_scales = [round(s * downscale, 10) for s in full_scales]
        best = _scan_scales(small, template.gray, small_scales)
        return None if best is None else best / downscale

    coarse = scan(_scales_between(round(lo, 10), round(hi, 10), _SCALE_COARSE_STEP))
    if coarse is None:
        return None
    fine_lo = max(lo, coarse - _SCALE_COARSE_STEP)
    fine_hi = min(hi, coarse + _SCALE_COARSE_STEP)
    fine_scales = sorted(
        set(_scales_between(round(fine_lo, 10), round(fine_hi, 10), _SCALE_FINE_STEP))
    )
    best = scan(fine_scales) or coarse

    tw_f, th_f = round(tw * best), round(th * best)
    if (tw_f, th_f) == (tw, th):
        return template
    interp = cv2.INTER_AREA if best < 1 else cv2.INTER_LINEAR
    return PreparedTemplate(
        rgb=cv2.resize(template.rgb, (tw_f, th_f), interpolation=interp),
        gray=cv2.resize(template.gray, (tw_f, th_f), interpolation=interp),
        hsv=cv2.resize(template.hsv, (tw_f, th_f), interpolation=interp),
    )


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
    template: PreparedTemplate,
    candidate: np.ndarray,
    candidate_hsv: np.ndarray,
) -> bool:
    """颜色验证(P3):三段门,同色轻微渲染差异接受、状态色不同拒绝(Q3)。

    1. H/S 通道去均值相关 ≥ COLOR_CONFIDENCE_MIN:色相/饱和度是状态色
       判别主通道(通道对调、局部色点都在此失去相关)。
    2. V 通道去均值相关 ≥ COLOR_VALUE_CONFIDENCE_MIN:明度图样一致性;
       单独放宽,因跨分辨率重采样的相位差会在细笔画上压低明度相关
       (实测 81px 彩色图标 1.25× 时 V=0.78 而 H/S≥0.93)。
    3. RGB 均值绝对差 ≤ COLOR_MEAN_DIFF_MAX:去均值相关对均匀线性偏移
       不敏感,单独用 NCC 会放过整体变暗/去饱和的禁用态;此门承袭 v1
       语义补上该缺口(v1 对均匀变暗 30% 实测均值差 ~60 > 16 拒绝)。
    """
    diff = float(
        np.abs(candidate.astype(np.int16) - template.rgb.astype(np.int16)).mean()
    )
    if diff > COLOR_MEAN_DIFF_MAX:
        return False
    ncc = [
        _channel_ncc(candidate_hsv[:, :, i], template.hsv[:, :, i]) for i in range(3)
    ]
    return min(ncc[0], ncc[1]) >= COLOR_CONFIDENCE_MIN and ncc[2] >= (
        COLOR_VALUE_CONFIDENCE_MIN
    )
