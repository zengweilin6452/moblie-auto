"""原生定位选择规则:可见性过滤、index 语义与单目标歧义。"""

import pytest
from fakes import FakeElement

from mobile_auto import AmbiguousTarget, ElementNotFound, Locator, UnsupportedOperation
from mobile_auto.contracts import Target
from mobile_auto.selectors import (
    resolve_single,
    select_matches,
    validate_locatable,
    visible_only,
)

SCREEN = (1080, 2400)
ALLOWED = frozenset({Locator.RESOURCE_ID, Locator.TEXT})


def test_visible_only_filters_invalid_and_offscreen_bounds():
    elements = [
        FakeElement((0, 0, 100, 50)),        # 可见
        FakeElement(None),                    # 无 bounds
        FakeElement((10, 10, 10, 50)),        # 零宽
        FakeElement((50, 60, 40, 90)),        # 负宽高(right <= left)
        FakeElement((2000, 0, 3000, 100)),    # 屏幕右侧外
        FakeElement((0, 2500, 100, 2600)),    # 屏幕下侧外(top >= 屏高)
        FakeElement((-50, 0, 60, 100)),       # 部分越出左边界,相交仍可见
    ]
    visible = visible_only(elements, SCREEN)
    assert visible == [elements[0], elements[6]]


def test_visible_only_preserves_order():
    first = FakeElement((0, 0, 10, 10))
    second = FakeElement((20, 20, 30, 30))
    assert visible_only([second, first], SCREEN) == [second, first]


def test_select_matches_index_semantics():
    elements = ["a", "b", "c"]
    target_without = Target(Locator.TEXT, "x")
    assert select_matches(elements, target_without) == elements
    target_indexed = Target(Locator.TEXT, "x", index=1)
    assert select_matches(elements, target_indexed) == ["b"]
    target_overflow = Target(Locator.TEXT, "x", index=5)
    assert select_matches(elements, target_overflow) == []


def test_resolve_single_requires_existence_and_uniqueness():
    target = Target(Locator.TEXT, "确定")
    with pytest.raises(ElementNotFound):
        resolve_single([], target)
    with pytest.raises(AmbiguousTarget, match="2 个可见元素"):
        resolve_single(["a", "b"], target)
    assert resolve_single(["only"], target) == "only"
    assert resolve_single(["a", "b"], Target(Locator.TEXT, "确定", index=1)) == "b"


def test_validate_locatable_reports_future_stages():
    validate_locatable(Target(Locator.TEXT, "x"), ALLOWED)
    validate_locatable(Target(Locator.RESOURCE_ID, "x"), ALLOWED)
    with pytest.raises(UnsupportedOperation, match="图像定位工单"):
        validate_locatable(Target(Locator.IMAGE, "t.png"), ALLOWED)
    with pytest.raises(UnsupportedOperation, match="工单 03"):
        validate_locatable(Target(Locator.XPATH, "//node"), ALLOWED)
