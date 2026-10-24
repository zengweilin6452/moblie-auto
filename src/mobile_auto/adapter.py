"""MobileAdapter:异步公共接口与核心语义(plan 5.1/5.3)。

pytest 直接调用本适配器,不经 HTTP;所有设备访问经 DeviceSession 的
串行队列。单目标操作(click/get_text)在查找预算内短轮询等待目标出现,
每轮"定位-校验-动作"是同一个队列任务;轮询间隙允许取消,预算耗尽不再
发起新轮询。本工单(02)交付 resource_id/text 定位切片。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, TypeVar

from .contracts import ElementHandle, Locator, Target
from .errors import ConfigurationError, ElementNotFound, UnsupportedOperation
from .selectors import (
    make_handle,
    resolve_single,
    select_matches,
    validate_locatable,
    visible_only,
)
from .session import DeviceSession

T = TypeVar("T")

_NATIVE_LOCATORS = frozenset({Locator.RESOURCE_ID, Locator.TEXT})

_MISSING = object()  # 区分"本轮未找到"与找到的空值(如空字符串文本)


class MobileAdapter:
    """统一驱动的异步方法面;platform 固定 android。"""

    platform = "android"

    def __init__(self, session: DeviceSession) -> None:
        self._session = session

    async def _call(self, operation: Callable[[Any], T]) -> T:
        """提交到会话队列并等待;对未开始任务的取消传播由 wrap_future 提供。"""
        future: Future[T] = self._session.submit(operation)
        return await asyncio.wrap_future(future)

    def _locate(self, backend, target: Target) -> list:
        """单轮定位:校验定位方式 → 底层查找 → 可见性过滤,保持树遍历序。"""
        validate_locatable(target, _NATIVE_LOCATORS)
        return visible_only(
            backend.find_elements(target.by, target.value),
            backend.screen_size(),
        )

    async def _poll_until(self, attempt: Callable[[Any], T], target: Target) -> T:
        """在查找预算内轮询 attempt;歧义立即报,预算耗尽报 ElementNotFound。

        attempt 每轮返回结果或 _MISSING;AmbiguousTarget 等终止性错误直接
        传播,不重试。取消在轮询间隙生效,取消后不再发起新一轮查询。
        """
        config = self._session.config
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.wait_timeout
        while True:
            result = await self._call(attempt)
            if result is not _MISSING:
                return result
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ElementNotFound(
                    f"{target.by.value}={target.value!r} 在 {config.wait_timeout} 秒内"
                    "未出现可见唯一匹配"
                )
            await asyncio.sleep(min(config.poll_interval, remaining))

    async def goto(self, target: str = "") -> None:
        """空串冷启动已配置 package/activity(先停止再启动,起点确定);
        非空按 Deep Link 白名单校验。

        白名单为空(默认)时任何链接都立即报配置错误(plan 5.3);
        通过白名单校验的启动调用随工单 03 交付。
        """
        if target:
            schemes = self._session.config.allowed_deeplink_schemes
            scheme = target.split(":", 1)[0].lower()
            if not schemes or scheme not in schemes:
                raise ConfigurationError(
                    f"Deep Link scheme {scheme!r} 不在 allowed_deeplink_schemes 白名单中"
                )
            raise UnsupportedOperation("Deep Link 启动随后续工单交付")
        config = self._session.config
        if not config.app_package:
            raise ConfigurationError("未配置 app_package,无法启动 App")
        await self._call(
            lambda backend: backend.app_start(
                config.app_package, config.app_activity, stop=True
            )
        )

    async def find_elements(self, target: Target) -> list[ElementHandle]:
        """无序号返回全部可见匹配(树遍历序);有序号返回至多一个。"""

        def operation(backend):
            matches = self._locate(backend, target)
            return [
                make_handle(element, target)
                for element in select_matches(matches, target)
            ]

        return await self._call(operation)

    async def is_visible(self, target: Target) -> bool:
        """单次查询,不进行长等待。"""

        def operation(backend):
            return bool(select_matches(self._locate(backend, target), target))

        return await self._call(operation)

    async def wait_for(self, target: Target, timeout: float | None = None) -> bool:
        """默认 10 秒、轮询 0.5 秒;存在返回 True,预算耗尽返回 False。

        timeout=0 查询一次;负数或非有限值报 ValueError(调用方编程错误)。
        设备错误(查询抛错)原样传播,不折算为 False(plan 5.2)。
        任务取消在轮询间隙生效:取消后不再发起新一轮查询。
        """
        budget = self._session.config.wait_timeout if timeout is None else timeout
        if (
            isinstance(budget, bool)
            or not isinstance(budget, (int, float))
            or not math.isfinite(budget)
            or budget < 0
        ):
            raise ValueError(f"timeout 必须是有限的非负数值,得到 {timeout!r}")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        while True:
            if await self.is_visible(target):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(self._session.config.poll_interval, remaining))

    async def click(self, target: Target) -> None:
        """等待单目标可见(预算内轮询)后,在同一队列任务里发送一次点击。

        多匹配无序号立即报歧义;预算耗尽报 ElementNotFound。
        """

        def attempt(backend):
            try:
                element = resolve_single(self._locate(backend, target), target)
            except ElementNotFound:  # 本轮未出现:交给轮询继续
                return _MISSING
            element.click()
            return True

        await self._poll_until(attempt, target)

    async def get_text(self, target: Target) -> str:
        """等待单目标可见后返回原生文本;已知缺失归一为空字符串。"""

        def attempt(backend):
            try:
                element = resolve_single(self._locate(backend, target), target)
            except ElementNotFound:
                return _MISSING
            return element.text or ""

        return await self._poll_until(attempt, target)

    async def screenshot(self) -> bytes:
        """新采集画面显式编码为 PNG;不返回缓存帧。"""
        return await self._call(lambda backend: backend.screenshot_png())

    async def close(self) -> None:
        """幂等;仅结束本会话的调度,不停止 App。"""
        await asyncio.to_thread(self._session.close)
