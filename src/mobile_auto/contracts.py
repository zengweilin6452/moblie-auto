"""本地参考契约的基础类型:定位方式与定位描述(GLOSSARY“参考契约”)。

这些类型只在本包内有效;内网接入时由绑定层转换为真实类型,
不依赖两个同名类的 isinstance 兼容。CSS 定位明确不支持。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

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


__all__ = ["Locator", "Target"]
