"""拾取候选生成:从 UI 树节点生成定位描述候选与 Target 表达式。

表达式与驱动使用同一 Target 解析规则(表达式即构造代码);
易变属性(文本、序号)标注风险提示(plan 8.2)。
"""

from __future__ import annotations

import json

from ..contracts import Locator, Target
from ..core.ui_tree import UiNode

_RISK_STABLE = "stable"
_RISK_TEXT = "文本易变:页面文案变化后失效"
_RISK_POSITIONAL = "位置路径:布局顺序变化即失效,仅作兜底"


def _candidate(by: Locator, value: str, risk: str, note: str = "") -> dict:
    target = Target(by=by, value=value)
    return {
        "by": by.value,
        "value": value,
        "risk": risk,
        "note": note,
        "expression": (
            f'Target(Locator.{by.name}, {json.dumps(value, ensure_ascii=False)})'
        ),
        "target": {"by": target.by.value, "value": target.value},
    }


def candidates_for_node(node: UiNode) -> list[dict]:
    """为单个节点生成定位候选;同 (方式, 值) 去重,稳定方式在前。"""
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(by: Locator, value: str, risk: str, note: str = "") -> None:
        if not value or (by.value, value) in seen:
            return
        seen.add((by.value, value))
        candidates.append(_candidate(by, value, risk, note))

    add(Locator.RESOURCE_ID, node.resource_id, _RISK_STABLE)
    add(Locator.ACCESSIBILITY_ID, node.content_desc, _RISK_STABLE)
    if node.text:
        add(Locator.TEXT, node.text, _RISK_TEXT, "文案变化后需重新拾取")
    selector_fields = {}
    if node.resource_id:
        selector_fields["resourceId"] = node.resource_id
    if node.text:
        selector_fields["text"] = node.text
    if len(selector_fields) >= 2:
        add(
            Locator.U2_SELECTOR,
            json.dumps(selector_fields, ensure_ascii=False),
            _RISK_TEXT,
            "组合字段中含文本",
        )
    return candidates


def build_xpath(root: UiNode, target: UiNode) -> str:
    """生成根到目标的绝对 XPath(类名 + 同类序号);目标不在树中返回空串。"""
    chain = _path_to(root, target)
    if not chain:
        return ""
    segments = []
    for parent, node in zip(chain, chain[1:], strict=False):
        same = [c for c in parent.children if c.class_name == node.class_name]
        if len(same) > 1:
            segments.append(f"{node.class_name}[{same.index(node) + 1}]")
        else:
            segments.append(node.class_name)
    return "//" + "/".join(segments) if segments else ""


def _path_to(root: UiNode, target: UiNode) -> list[UiNode]:
    stack = [(root, [root])]
    while stack:
        node, chain = stack.pop()
        if node is target:
            return chain
        for child in node.children:
            stack.append((child, chain + [child]))
    return []


def xpath_candidate(root: UiNode, node: UiNode) -> dict | None:
    xpath = build_xpath(root, node)
    if not xpath:
        return None
    return _candidate(Locator.XPATH, xpath, _RISK_POSITIONAL, "绝对路径,兜底用")
