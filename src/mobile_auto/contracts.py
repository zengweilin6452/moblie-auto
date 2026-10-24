"""本地参考契约的基础类型:定位方式与定位描述(GLOSSARY“参考契约”)。

这些类型只在本包内有效;内网接入时由绑定层转换为真实类型,
不依赖两个同名类的 isinstance 兼容。CSS 定位明确不支持。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .errors import InvalidTarget


class Locator(StrEnum):
    """支持的定位方式;成员值即配置与网页接口中使用的枚举字符串。"""

    RESOURCE_ID = "resource_id"
    TEXT = "text"
    ACCESSIBILITY_ID = "accessibility_id"
    U2_SELECTOR = "u2_selector"
    XPATH = "xpath"
    IMAGE = "image"


@dataclass(frozen=True)
class Target:
    """定位描述:描述如何寻找界面目标的不可变数据(GLOSSARY“定位描述”)。

    可对当前画面重新求值;不持有设备或节点状态。
    region 为 (x, y, width, height) 四整数,语义为图像模板的搜索区域;
    是否落在截图内由匹配阶段校验,这里只校验类型。
    """

    by: Locator
    value: str
    index: int | None = None
    confidence: float | None = None
    region: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.by, str):
            try:
                object.__setattr__(self, "by", Locator(self.by))
            except ValueError as exc:
                raise InvalidTarget(f"不支持的定位方式: {self.by!r}") from exc
        if not isinstance(self.by, Locator):
            raise InvalidTarget(f"by 必须是 Locator,得到 {type(self.by).__name__}")
        if not isinstance(self.value, str) or not self.value:
            raise InvalidTarget("value 必须是非空字符串")
        if self.index is not None and (
            isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0
        ):
            raise InvalidTarget("index 必须是非负整数,不接受 bool、负数和字符串数字")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0 < self.confidence <= 1
        ):
            raise InvalidTarget("confidence 必须是 (0, 1] 区间内的有限数值")
        if self.region is not None:
            region = self.region
            if (
                len(region) != 4
                or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
            ):
                raise InvalidTarget("region 必须是四个整数 (x, y, width, height)")
            object.__setattr__(self, "region", tuple(region))


@dataclass(frozen=True)
class ElementHandle:
    """查询句柄:某次查询结果的引用(GLOSSARY“查询句柄”)。

    携带重校验依据;过期即失效,不自动指向新节点。重用前重新定位并
    校验关键属性(className、resourceId、contentDescription;文本易变
    不参与身份),会话改变或属性不一致报 StaleHandle。
    owner 为绑定会话,仅用于路由操作,不参与相等比较与展示。
    """

    target: Target
    bounds: tuple[int, int, int, int]
    text: str = ""
    class_name: str = ""
    resource_id: str = ""
    content_desc: str = ""
    owner: Any = field(default=None, compare=False, repr=False)

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.class_name, self.resource_id, self.content_desc)

    async def _submit(self, use_node) -> Any:
        import asyncio

        from .errors import StaleHandle

        if self.owner is None:
            raise StaleHandle("句柄未绑定会话,无法执行操作;请通过 adapter.find_elements 获取")
        from .selectors import revalidate_handle

        def operation(backend):
            node = revalidate_handle(backend, self)
            return use_node(node)

        future = self.owner.submit(operation)
        return await asyncio.wrap_future(future)

    async def click(self) -> None:
        """句柄直接点击;身份无法确认报 StaleHandle,不重定向。"""
        await self._submit(lambda node: node.click())

    async def get_text(self) -> str:
        """句柄直接取文本;节点消失或身份变化报 StaleHandle。"""
        return await self._submit(lambda node: node.text or "")

    async def input_text(self, text: str) -> None:
        """句柄直接输入:清空并替换;不可编辑目标明确失败。"""
        from .selectors import ensure_editable

        def use_node(node) -> None:
            ensure_editable(getattr(node, "info", {}) or {})
            node.clear_text()
            node.set_text(text)

        await self._submit(use_node)

    async def get_attribute(self, name: str) -> str | tuple[int, int, int, int]:
        from .selectors import attribute_value, validate_attribute_name

        validate_attribute_name(name)
        return await self._submit(
            lambda node: attribute_value(getattr(node, "info", {}) or {}, name)
        )


__all__ = ["ElementHandle", "Locator", "Target"]
