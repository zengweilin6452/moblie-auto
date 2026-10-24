"""UI 树获取与归一化:节点、父子关系、bounds 与属性(plan 10)。

产出供失败证据与网页复用的只读树;解析失败由调用方按场景处理
(证据降级留因,网页报能力错误)。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

_ROOT_CLASS = "root"


@dataclass(frozen=True)
class UiNode:
    """归一化 UI 节点;children 保持文档顺序。"""

    class_name: str = ""
    resource_id: str = ""
    text: str = ""
    content_desc: str = ""
    bounds: tuple[int, int, int, int] | None = None
    clickable: bool = False
    enabled: bool = True
    selected: bool = False
    checked: bool = False
    children: tuple[UiNode, ...] = ()


def parse_ui_hierarchy(xml: str) -> UiNode:
    """解析 uiautomator 层级 XML;顶层包装为 class_name='root' 的虚拟根。

    非合法 XML 或空串抛 xml.etree.ElementTree.ParseError,由调用方归因。
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    return UiNode(class_name=_ROOT_CLASS, children=tuple(_node(child) for child in root))


def _node(element) -> UiNode:
    return UiNode(
        class_name=element.get("class") or "",
        resource_id=element.get("resource-id") or "",
        text=element.get("text") or "",
        content_desc=element.get("content-desc") or "",
        bounds=_parse_bounds(element.get("bounds") or ""),
        clickable=element.get("clickable") == "true",
        enabled=element.get("enabled") != "false",
        selected=element.get("selected") == "true",
        checked=element.get("checked") == "true",
        children=tuple(_node(child) for child in element),
    )


def _parse_bounds(text: str) -> tuple[int, int, int, int] | None:
    match = _BOUNDS_RE.fullmatch(text)
    if not match:
        return None
    left, top, right, bottom = (int(part) for part in match.groups())
    return left, top, right, bottom


def flatten(node: UiNode) -> Iterator[UiNode]:
    """深度优先遍历(含自身),顺序即树遍历序。"""
    yield node
    for child in node.children:
        yield from flatten(child)
