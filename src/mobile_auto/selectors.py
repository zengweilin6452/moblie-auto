"""原生 Target 解析、可见性过滤与匹配选择(plan 5.2)。

规则:RESOURCE_ID/TEXT 精确匹配(由底层选择器保证);可见性为有效非空
bounds 且与屏幕相交;无 index 判断存在性或枚举全部,单目标操作要求唯一。
"""

from __future__ import annotations

from typing import Protocol

from .contracts import ElementHandle, Locator, Target
from .errors import AmbiguousTarget, ElementNotFound, UnsupportedOperation

_STAGE_LOCATORS: dict[Locator, str] = {
    Locator.ACCESSIBILITY_ID: "工单 03",
    Locator.U2_SELECTOR: "工单 03",
    Locator.XPATH: "工单 03",
    Locator.IMAGE: "图像定位工单",
}


def validate_locatable(target: Target, allowed: frozenset[Locator]) -> None:
    """校验定位方式在当前能力范围内;未交付的方式报不支持。"""
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
    """单目标操作的选择:零匹配报不存在,多匹配无序号报歧义。"""
    if not elements:
        raise ElementNotFound(
            f"未找到可见匹配:{target.by.value}={target.value!r}"
            + (f"[{target.index}]" if target.index is not None else "")
        )
    if target.index is None and len(elements) > 1:
        raise AmbiguousTarget(
            f"定位 {target.by.value}={target.value!r} 匹配到 {len(elements)} 个可见元素,"
            "未指定序号;请改用 index 或收窄定位"
        )
    return elements[target.index] if target.index is not None else elements[0]


def make_handle(element, target: Target) -> ElementHandle:
    """把一次匹配固化为查询句柄;重校验依据随工单 03 扩充。"""
    return ElementHandle(
        target=target,
        bounds=element.bounds,
        text=element.text,
    )
