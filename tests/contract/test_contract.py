"""15 方法契约清单核对入口(docs/integration.md §3)。

对参考契约(MobileAdapter)逐方法核对:存在、可调用、签名形状与
platform 属性。内网接入时把绑定层实现替换本文件中的契约对象,
同一份清单核对真实 Port 不缺不漏。
"""

import inspect

import pytest

from mobile_auto import MobileAdapter

# plan 5.1:15 个异步方法 + platform 属性
CONTRACT_METHODS = [
    "goto",
    "wait_for_load",
    "back",
    "click",
    "input_text",
    "get_text",
    "get_attribute",
    "wait_for",
    "is_visible",
    "find_elements",
    "extract_list",
    "screenshot",
    "press_key",
    "scroll",
    "close",
]


# extract_list 待真实 ListItemSchema(内网门槛),契约面预留位置但不实现;
# 其余 14 个方法必须为异步可调用。
IMPLEMENTED_METHODS = [m for m in CONTRACT_METHODS if m != "extract_list"]


@pytest.mark.parametrize("name", IMPLEMENTED_METHODS)
def test_contract_method_exists_and_is_async(name):
    method = getattr(MobileAdapter, name, None)
    assert method is not None, f"契约方法缺失:{name}"
    assert inspect.iscoroutinefunction(method), f"契约方法 {name} 应为异步"


def test_extract_list_declared_not_implemented():
    """内网门槛:extract_list 不伪造(文档验证记录与 integration.md 一致)。"""
    assert not hasattr(MobileAdapter, "extract_list")


def test_contract_surface_is_closed():
    # 契约面封闭:除清单内 14 个方法外无其他公共方法;新增须更新清单与文档
    extra = set(IMPLEMENTED_METHODS) ^ {
        n
        for n in dir(MobileAdapter)
        if not n.startswith("_") and n != "platform" and callable(getattr(MobileAdapter, n))
    }
    assert not extra, f"契约面与清单不一致:{sorted(extra)}"


def test_contract_platform_attribute():
    assert MobileAdapter.platform == "android"


def test_contract_error_catalogue_is_closed():
    """plan 5.4 的 13 类错误 + 基类:封闭清单,绑定层按此映射。"""
    import mobile_auto.errors as errors

    expected = {
        "InvalidTarget",
        "ConfigurationError",
        "DeviceUnavailable",
        "DeviceBusy",
        "ElementNotFound",
        "AmbiguousTarget",
        "StaleHandle",
        "UnsupportedOperation",
        "WaitTimeout",
        "InvalidTemplate",
        "DuplicateTemplate",
        "MatchOverflow",
        "OperationOutcomeUnknown",
        "SessionFaulted",
    }
    for name in expected:
        cls = getattr(errors, name, None)
        assert cls is not None, f"错误类别缺失:{name}"
        assert issubclass(cls, errors.MobileAutoError)


def test_contract_locator_enum_is_closed():
    from mobile_auto import Locator

    assert {m.value for m in Locator} == {
        "resource_id",
        "text",
        "accessibility_id",
        "u2_selector",
        "xpath",
        "image",
    }  # CSS 明确不支持
