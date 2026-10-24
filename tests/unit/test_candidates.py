"""拾取候选生成:五种定位方式、风险标注与 XPath 位置路径。"""

from mobile_auto.ui_tree import parse_ui_hierarchy
from mobile_auto.viewer.candidates import (
    build_xpath,
    candidates_for_node,
    xpath_candidate,
)

XML = """<?xml version='1.0'?><hierarchy>
<node class="android.widget.FrameLayout" text="" resource-id=""
      bounds="[0,0][150,200]">
  <node class="android.widget.LinearLayout" text="" resource-id="" bounds="[0,0][150,200]">
    <node class="android.widget.TextView" text="WLAN" resource-id="com.demo:id/row"
          content-desc="无线网络" bounds="[10,20][80,40]"/>
    <node class="android.widget.TextView" text="副标题" resource-id="" bounds="[10,40][80,60]"/>
  </node>
</node></hierarchy>"""

ROOT = parse_ui_hierarchy(XML)


def find_node(text):
    def walk(node):
        if node.text == text:
            return node
        for child in node.children:
            found = walk(child)
            if found:
                return found
        return None

    return walk(ROOT)


def test_candidate_covers_stable_text_and_selector_modes():
    node = find_node("WLAN")
    candidates = candidates_for_node(node)
    modes = [c["by"] for c in candidates]
    assert modes == ["resource_id", "accessibility_id", "text", "u2_selector"]
    by_mode = {c["by"]: c for c in candidates}
    assert by_mode["resource_id"]["value"] == "com.demo:id/row"
    assert by_mode["resource_id"]["risk"] == "stable"
    assert "文本易变" in by_mode["text"]["risk"]
    assert '"resourceId"' in by_mode["u2_selector"]["value"]


def test_candidate_without_stable_ids_only_text():
    node = find_node("副标题")
    candidates = candidates_for_node(node)
    assert [c["by"] for c in candidates] == ["text"]  # 无 resource_id/描述:仅文本且标险
    assert "文本易变" in candidates[0]["risk"]


def test_expression_uses_same_target_rule():
    node = find_node("WLAN")
    candidate = candidates_for_node(node)[0]
    assert candidate["expression"] == (
        'Target(Locator.RESOURCE_ID, "com.demo:id/row")'
    )
    # 表达式即构造参数:target 字段可直接构造同一 Target(同一解析规则)
    from mobile_auto import Locator, Target

    target = Target(**{
        "by": Locator(candidate["target"]["by"]),
        "value": candidate["target"]["value"],
    })
    assert target.by is Locator.RESOURCE_ID


def test_xpath_positional_path_and_risk():
    wlan = find_node("WLAN")
    subtitle = find_node("副标题")
    # 两个同类 TextView:同类序号 [1]/[2] 均用于消歧
    assert build_xpath(ROOT, wlan) == (
        "//android.widget.FrameLayout/android.widget.LinearLayout/"
        "android.widget.TextView[1]"
    )
    assert "[2]" in build_xpath(ROOT, subtitle)
    candidate = xpath_candidate(ROOT, subtitle)
    assert candidate is not None
    assert "位置路径" in candidate["risk"]


def test_xpath_unknown_node_returns_empty():
    stranger = parse_ui_hierarchy(XML)
    assert build_xpath(ROOT, find_or_first(stranger)) == ""


def find_or_first(root):
    return root.children[0].children[0] if root.children else root
