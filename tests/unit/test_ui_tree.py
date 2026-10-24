"""UI 树归一化:节点、父子关系、bounds 与属性。"""

import pytest

from mobile_auto.core.ui_tree import flatten, parse_ui_hierarchy

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.demo" content-desc="" checkable="false" checked="false"
        clickable="false" enabled="true" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="WLAN" resource-id="com.demo:id/row"
          class="android.widget.TextView" package="com.demo"
          content-desc="无线网络" checkable="false" checked="true"
          clickable="true" enabled="false" selected="true"
          bounds="[0,100][500,200]"/>
  </node>
</hierarchy>
"""


def test_parse_ui_hierarchy_builds_normalized_tree():
    root = parse_ui_hierarchy(SAMPLE_XML)
    assert root.class_name == "root"
    frame = root.children[0]
    assert frame.class_name == "android.widget.FrameLayout"
    assert frame.bounds == (0, 0, 1080, 2400)
    (row,) = frame.children
    assert row.text == "WLAN"
    assert row.resource_id == "com.demo:id/row"
    assert row.content_desc == "无线网络"
    assert row.bounds == (0, 100, 500, 200)
    assert row.clickable is True
    assert row.enabled is False
    assert row.selected is True
    assert row.checked is True


def test_flatten_yields_depth_first_order():
    root = parse_ui_hierarchy(SAMPLE_XML)
    nodes = list(flatten(root))
    assert [n.class_name for n in nodes] == [
        "root",
        "android.widget.FrameLayout",
        "android.widget.TextView",
    ]


def test_parse_rejects_invalid_xml():
    from xml.etree import ElementTree

    with pytest.raises(ElementTree.ParseError):
        parse_ui_hierarchy("")
    with pytest.raises(ElementTree.ParseError):
        parse_ui_hierarchy("<hierarchy><node></hierarchy>")
