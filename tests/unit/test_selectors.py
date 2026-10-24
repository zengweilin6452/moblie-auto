"""原生定位选择规则:可见性过滤、index 语义、歧义与 u2 选择器白名单。"""

import pytest
from fakes import FakeElement

from mobile_auto import (
    AmbiguousTarget,
    ElementNotFound,
    InvalidTarget,
    Locator,
    UnsupportedOperation,
)
from mobile_auto.contracts import Target
from mobile_auto.selectors import (
    NATIVE_LOCATORS,
    parse_u2_selector,
    resolve_single,
    scroll_points,
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


def test_validate_locatable_checks_u2_selector_json_upfront():
    with pytest.raises(InvalidTarget, match="合法 JSON"):
        validate_locatable(Target(Locator.U2_SELECTOR, "{bad"), NATIVE_LOCATORS)
    validate_locatable(
        Target(Locator.U2_SELECTOR, '{"text": "WLAN"}'), NATIVE_LOCATORS
    )


def test_parse_u2_selector_whitelist():
    parsed = parse_u2_selector('{"resourceId": "com.demo:id/ok", "clickable": true}')
    assert parsed == {"resourceId": "com.demo:id/ok", "clickable": True}
    with pytest.raises(InvalidTarget, match="未知字段"):
        parse_u2_selector('{"textContains": "测"}')  # 模糊匹配字段不在白名单
    with pytest.raises(InvalidTarget, match="未知字段"):
        parse_u2_selector('{"matches": "^W"}')  # 正则字段明确拒绝
    with pytest.raises(InvalidTarget, match="非空字符串"):
        parse_u2_selector('{"text": 1}')
    with pytest.raises(InvalidTarget, match="布尔值"):
        parse_u2_selector('{"clickable": "true"}')
    with pytest.raises(InvalidTarget, match="JSON object"):
        parse_u2_selector('"text=WLAN"')
    with pytest.raises(InvalidTarget, match="合法 JSON"):
        parse_u2_selector("")


def test_scroll_points_direction_and_clamping():
    # down = 手指向上:起点在下、终点在上
    (sx, sy), (ex, ey) = scroll_points(SCREEN, "down", 500)
    assert (sx, ex) == (540, 540) and sy > ey
    # up = 手指向下
    (_, sy2), (_, ey2) = scroll_points(SCREEN, "up", 500)
    assert sy2 < ey2
    # amount 超过安全区域时夹限:最大距离为屏高的 80%
    (_, sy3), (_, ey3) = scroll_points(SCREEN, "down", 10**9)
    assert 2400 * 0.8 - 1 <= sy3 - ey3 <= 2400 * 0.8 + 1
    # 左右方向作用于横向
    (sx4, _), (ex4, _) = scroll_points(SCREEN, "right", 300)
    assert sx4 > ex4  # 视口右移,手指向左
