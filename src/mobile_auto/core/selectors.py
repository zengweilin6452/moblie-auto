"""原生 Target 解析、可见性过滤、匹配选择与句柄校验(plan 5.2/6.2)。

规则:五种原生定位方式的精确匹配由底层选择器保证(u2 选择器在本模块
做 JSON 白名单校验);可见性为有效非空 bounds 且与屏幕相交;无 index
判断存在性或枚举全部,单目标操作要求唯一;index 拒绝 bool、负数与
字符串数字(Target 构造时校验)。
"""

from __future__ import annotations

import json
from typing import Protocol

from ..contracts import ElementHandle, Locator, Target
from ..errors import (
    AmbiguousTarget,
    ElementNotFound,
    InvalidTarget,
    StaleHandle,
    UnsupportedOperation,
)

_STAGE_LOCATORS: dict[Locator, str] = {
    Locator.IMAGE: "图像定位工单",
}

# 五种原生定位方式;CSS 明确不支持(Locator 枚举中不存在)。
NATIVE_LOCATORS = frozenset(
    {
        Locator.RESOURCE_ID,
        Locator.TEXT,
        Locator.ACCESSIBILITY_ID,
        Locator.U2_SELECTOR,
        Locator.XPATH,
    }
)

# u2 选择器 JSON 白名单(plan 5.2):值类型逐项校验,拒绝代码、正则与未知字段。
_U2_SELECTOR_STR_FIELDS = (
    "resourceId",
    "text",
    "description",
    "className",
    "packageName",
)
_U2_SELECTOR_BOOL_FIELDS = ("clickable", "enabled", "selected", "checked")


def parse_u2_selector(value: str) -> dict:
    """解析并校验 u2 选择器 JSON object 字符串,返回可直接交给后端的字典。"""
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidTarget(f"u2 选择器不是合法 JSON:{exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidTarget("u2 选择器必须是 JSON object,不接受字符串或数组")
    unknown = sorted(
        set(parsed) - set(_U2_SELECTOR_STR_FIELDS) - set(_U2_SELECTOR_BOOL_FIELDS)
    )
    if unknown:
        raise InvalidTarget(f"u2 选择器包含未知字段:{', '.join(unknown)}")
    for key, val in parsed.items():
        if key in _U2_SELECTOR_STR_FIELDS:
            if not isinstance(val, str) or not val:
                raise InvalidTarget(f"u2 选择器字段 {key} 必须是非空字符串")
        elif not isinstance(val, bool):
            raise InvalidTarget(f"u2 选择器字段 {key} 必须是布尔值")
    return parsed


def validate_locatable(target: Target, allowed: frozenset[Locator]) -> None:
    """校验定位方式在当前能力范围内;未交付的方式报不支持。"""
    if target.by is Locator.U2_SELECTOR:
        parse_u2_selector(target.value)  # 提前失败,避免进入设备调用后才报错
    if target.by not in allowed:
        stage = _STAGE_LOCATORS.get(target.by)
        detail = f"将随{stage}交付" if stage else "不支持"
        raise UnsupportedOperation(f"定位方式 {target.by.value} 当前不可用({detail})")


class _BoundElement(Protocol):
    @property
    def bounds(self) -> tuple[int, int, int, int] | None: ...


def visible_only(
    elements: list[_BoundElement], screen: tuple[int, int]
) -> list[_BoundElement]:
    """过滤出可见元素:有效非空 bounds 且与屏幕矩形相交,保持树遍历顺序。"""
    width, height = screen
    visible = []
    for element in elements:
        bounds = element.bounds
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            continue
        if left >= width or top >= height or right <= 0 or bottom <= 0:
            continue
        visible.append(element)
    return visible


def select_matches(elements: list, target: Target) -> list:
    """index 语义:无序号返回全部;有序号返回至多一个(越界为空)。"""
    if target.index is None:
        return list(elements)
    if target.index < len(elements):
        return [elements[target.index]]
    return []


def resolve_single(elements: list, target: Target):
    """单目标操作的选择:零匹配或序号越界报不存在,多匹配无序号报歧义。"""
    if not elements:
        raise ElementNotFound(
            f"未找到可见匹配:{target.by.value}={target.value!r}"
            + (f"[{target.index}]" if target.index is not None else "")
        )
    if target.index is None:
        if len(elements) > 1:
            raise AmbiguousTarget(
                f"定位 {target.by.value}={target.value!r} 匹配到 {len(elements)} 个可见元素,"
                "未指定序号;请改用 index 或收窄定位"
            )
        return elements[0]
    if target.index >= len(elements):
        # 越界与零匹配同语义:本轮未出现,交由单目标轮询继续,不泄漏 IndexError
        raise ElementNotFound(
            f"未找到可见匹配:{target.by.value}={target.value!r}[{target.index}]"
        )
    return elements[target.index]


def identity_of(info: dict) -> tuple[str, str, str]:
    """节点关键属性(className、resourceId、contentDescription),用于句柄重校验。

    文本易变,不参与身份判定(plan 6.2)。
    """
    return (
        info.get("className") or "",
        info.get("resourceName") or "",
        info.get("contentDescription") or "",
    )


def make_handle(
    element, target: Target, owner=None, match_count: int | None = None
) -> ElementHandle:
    """把一次匹配固化为查询句柄;重校验依据取自该次快照。

    match_count 为创建时该定位的可见匹配总数:重校验时数量不一致说明
    匹配列表结构已变化,序号不可再信任。
    """
    info = getattr(element, "info", {}) or {}
    identity = identity_of(info)
    bounds = element.bounds
    if bounds is None:  # 不可见节点不会进入句柄;防御性兜底
        bounds = (0, 0, 0, 0)
    return ElementHandle(
        target=target,
        bounds=bounds,
        text=element.text,
        class_name=identity[0],
        resource_id=identity[1],
        content_desc=identity[2],
        match_count=match_count,
        owner=owner,
    )


def revalidate_handle(backend, handle: ElementHandle):
    """句柄重用:重新定位并校验关键属性、匹配数与位置;无法确认报 StaleHandle。

    不重定向到新节点:目标消失、歧义、属性变化、匹配列表增减或位置
    移动都不视为找到同一节点——同类节点在列表前方插入时,新节点会
    恰好继承原序号与原属性,仅凭"序号+关键属性"会点错,故结构或
    位置对不上即过期。
    """
    target = handle.target
    validate_locatable(target, NATIVE_LOCATORS)
    matches = visible_only(
        backend.find_elements(target.by, target.value),
        backend.screen_size(),
    )
    try:
        node = resolve_single(matches, target)
    except (ElementNotFound, AmbiguousTarget) as exc:
        raise StaleHandle(
            f"句柄目标 {target.by.value}={target.value!r} 无法再次唯一确认"
        ) from exc
    if identity_of(getattr(node, "info", {}) or {}) != handle.identity:
        raise StaleHandle("句柄关键属性已变化,拒绝指向新节点;请重新查找")
    if handle.match_count is not None and len(matches) != handle.match_count:
        raise StaleHandle(
            f"可见匹配数已变化({handle.match_count} → {len(matches)}),"
            "序号无法确认同一节点;请重新查找"
        )
    if tuple(node.bounds) != tuple(handle.bounds):
        raise StaleHandle(
            f"句柄目标位置已变化(原 {handle.bounds},现 {node.bounds}),"
            "不能确认同一节点;请重新查找"
        )
    return node


IMAGE_HANDLE_IOU_MIN = 0.5  # 图像句柄位置一致性下限(plan 6.2;校准初值)


def image_match_kwargs(template_meta: dict, target: Target) -> dict:
    """模板 meta + Target → 引擎匹配参数;图像查询与句柄重校验共用。

    两条路径必须用同一套 confidence/region/source_resolution/
    capture_center:查询走采集位置先验、重校验走全图时,远处同图
    实例会让刚查到的句柄重校验即报 StaleHandle。
    """
    from ..imaging.engine import validate_capture_center

    source = (template_meta.get("source_width"), template_meta.get("source_height"))
    return {
        "confidence": target.confidence,
        "region": target.region,
        "source_resolution": (
            source if all(isinstance(v, int) for v in source) else None
        ),
        "capture_center": validate_capture_center(template_meta.get("capture_center")),
    }


def make_image_handle(target: Target, result, template_meta: dict, screen, owner=None):
    """把一次图像匹配固化为句柄:模板摘要、原边界、屏幕尺寸与方向、阈值与区域。"""
    orientation = "portrait" if screen[1] >= screen[0] else "landscape"
    return ElementHandle(
        target=target,
        bounds=(result.x, result.y, result.width, result.height),
        image_sha256=template_meta["image_sha256"],
        screen_size=(int(screen[0]), int(screen[1])),
        orientation=orientation,
        owner=owner,
    )


class ImageNode:
    """图像匹配命中的可操作节点:点击落到结果中心。"""

    def __init__(self, backend, result) -> None:
        self._backend = backend
        self._result = result

    @property
    def bounds(self):
        return (self._result.x, self._result.y, self._result.width, self._result.height)

    def click(self) -> None:
        self._backend.tap(*self._result.center)


def revalidate_image_handle(backend, handle: ElementHandle, store) -> ImageNode:
    """图像句柄重用:校验模板摘要、会话、尺寸方向与位置一致性(plan 6.2)。

    重新匹配经 image_match_kwargs 与查询路径共用参数(含采集位置先验
    与跨分辨率尺度),任一身份依据无法确认报 StaleHandle,不重定向到
    新位置。
    """
    target = handle.target
    template = store.load(target.value)
    if template.sha256 != handle.image_sha256:
        raise StaleHandle("模板已更新为新版本;请重新查找图像目标")
    screen = backend.screen_size()
    if tuple(screen) != tuple(handle.screen_size or ()):
        raise StaleHandle(f"屏幕尺寸变化:{screen} != {handle.screen_size}")
    orientation = "portrait" if screen[1] >= screen[0] else "landscape"
    if orientation != handle.orientation:
        raise StaleHandle(f"屏幕方向变化:{orientation}")
    from ..imaging.engine import match_prepared, prepare_template

    prepared = prepare_template(template.png, what=f"模板 {target.value}")
    results = match_prepared(
        backend.screenshot_png(),
        prepared,
        **image_match_kwargs(template.meta, target),
    )
    matches = select_matches(results, target)
    if not matches:
        raise StaleHandle(
            f"图像目标 {target.value!r} 重新匹配无结果;页面可能已变化"
        )
    chosen = matches[0]
    old = handle.bounds
    new = (chosen.x, chosen.y, chosen.width, chosen.height)
    if _iou(old, new) < IMAGE_HANDLE_IOU_MIN:
        raise StaleHandle(
            f"图像目标位置偏移(原 {old},现 {new}),不能确认同一目标;请重新查找"
        )
    return ImageNode(backend, chosen)


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, ix2 - ix) * max(0, iy2 - iy)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


_BOOL_ATTRIBUTES = ("enabled", "clickable", "selected", "checked")
_KNOWN_ATTRIBUTES = (
    "text",
    "resourceId",
    "contentDescription",
    "className",
    "packageName",
    "bounds",
) + _BOOL_ATTRIBUTES


def validate_attribute_name(name: str) -> None:
    if name not in _KNOWN_ATTRIBUTES:
        raise InvalidTarget(
            f"未知属性:{name!r};支持:{', '.join(_KNOWN_ATTRIBUTES)}"
        )


def attribute_value(info: dict, name: str) -> str | tuple[int, int, int, int]:
    """读取节点属性:布尔以 true/false 字符串返回,bounds 为固定四整数,
    已知但缺失返回空串(plan 5.3)。"""
    validate_attribute_name(name)
    if name == "text":
        return info.get("text") or ""
    if name == "resourceId":
        return info.get("resourceName") or ""
    if name == "contentDescription":
        return info.get("contentDescription") or ""
    if name == "className":
        return info.get("className") or ""
    if name == "packageName":
        return info.get("packageName") or ""
    if name in _BOOL_ATTRIBUTES:
        return "true" if info.get(name) else "false"
    bounds = info.get("bounds") or {}
    try:
        return (
            int(bounds["left"]),
            int(bounds["top"]),
            int(bounds["right"]),
            int(bounds["bottom"]),
        )
    except (KeyError, TypeError):
        return ""  # 已知但缺失


def ensure_editable(info: dict) -> None:
    """拒绝不可编辑目标:以 className 判定(u2 info 无 editable 字段)。"""
    class_name = info.get("className") or ""
    if "EditText" not in class_name:
        raise InvalidTarget(
            f"目标不可编辑(className={class_name or '未知'});"
            "input_text 仅支持输入框"
        )


_SAFE_MARGIN_RATIO = 0.1


def scroll_points(
    screen: tuple[int, int], direction: str, amount: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    """滚动方向语义(plan 5.3):direction 为视口沿内容移动方向,
    down 对应手指向上。amount 为正整数像素,夹限到屏幕安全区域;
    返回 (起点, 终点) 坐标。"""
    width, height = screen
    if direction in ("down", "up"):
        lo, hi = int(height * _SAFE_MARGIN_RATIO), int(height * (1 - _SAFE_MARGIN_RATIO))
        distance = max(1, min(int(amount), hi - lo))
        center = (lo + hi) // 2
        if direction == "down":  # 手指向上:起点在下、终点在上
            start, end = center + distance // 2, center - distance // 2
        else:
            start, end = center - distance // 2, center + distance // 2
        return (width // 2, start), (width // 2, end)
    lo, hi = int(width * _SAFE_MARGIN_RATIO), int(width * (1 - _SAFE_MARGIN_RATIO))
    distance = max(1, min(int(amount), hi - lo))
    center = (lo + hi) // 2
    if direction == "right":  # 视口右移:手指向左
        start, end = center + distance // 2, center - distance // 2
    else:
        start, end = center - distance // 2, center + distance // 2
    return (start, height // 2), (end, height // 2)
