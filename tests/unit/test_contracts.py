"""定位描述契约的外部行为:类型校验与不可变性。"""

import dataclasses

import pytest

from mobile_auto import InvalidTarget, Locator, Target


def test_locator_only_supports_six_modes():
    assert {member.value for member in Locator} == {
        "resource_id",
        "text",
        "accessibility_id",
        "u2_selector",
        "xpath",
        "image",
    }


def test_target_accepts_enum_or_enum_string():
    assert Target(Locator.TEXT, "登录").by is Locator.TEXT
    assert Target("resource_id", "com.demo:id/ok").by is Locator.RESOURCE_ID


@pytest.mark.parametrize(
    ("kwargs",),
    [
        ({"by": "css", "value": "#ok"},),  # CSS 明确不支持
        ({"by": Locator.TEXT, "value": ""},),
        ({"by": Locator.TEXT, "value": "x", "index": -1},),
        ({"by": Locator.TEXT, "value": "x", "index": True},),
        ({"by": Locator.TEXT, "value": "x", "index": "0"},),
        ({"by": Locator.IMAGE, "value": "t.png", "confidence": 0},),
        ({"by": Locator.IMAGE, "value": "t.png", "confidence": 1.5},),
        ({"by": Locator.IMAGE, "value": "t.png", "confidence": True},),
        ({"by": Locator.IMAGE, "value": "t.png", "region": (1, 2, 3)},),
        ({"by": Locator.IMAGE, "value": "t.png", "region": (1, 2, 3.0, 4)},),
    ],
)
def test_invalid_target_rejected(kwargs):
    with pytest.raises(InvalidTarget):
        Target(**kwargs)


def test_target_boundary_values_accepted():
    target = Target(
        Locator.IMAGE,
        "buttons/ok.png",
        index=0,
        confidence=1.0,
        region=(0, 0, 100, 50),
    )
    assert target.region == (0, 0, 100, 50)
    assert target.confidence == 1.0


def test_target_is_immutable():
    target = Target(Locator.TEXT, "登录")
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.value = "首页"
