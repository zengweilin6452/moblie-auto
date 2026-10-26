"""MobileAdapter:异步公共接口与核心语义(plan 5.1/5.3)。

pytest 直接调用本适配器,不经 HTTP;所有设备访问经 DeviceSession 的
串行队列。单目标操作(click/input_text/get_text/get_attribute)在查找
预算内短轮询等待目标出现,每轮"定位-校验-动作"是同一个队列任务;轮询
间隙允许取消,预算耗尽不再发起新轮询。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, TypeVar

from ..contracts import ElementHandle, Target
from ..errors import (
    AmbiguousTarget,
    ConfigurationError,
    DeviceUnavailable,
    ElementNotFound,
    InvalidTarget,
    UnsupportedOperation,
    WaitTimeout,
)
from ..evidence.journal import journaled
from .selectors import (
    NATIVE_LOCATORS,
    attribute_value,
    ensure_editable,
    image_match_kwargs,
    make_handle,
    make_image_handle,
    resolve_single,
    scroll_points,
    select_matches,
    validate_attribute_name,
    validate_locatable,
    visible_only,
)
from .session import DeviceSession

T = TypeVar("T")

_PRESS_KEYS = frozenset({"back", "home", "enter", "search", "delete", "recent"})
_SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})

_IMAGE_FRESH_SECONDS = 1.0  # 采集完成到发送点击的新鲜度上限(plan 6.2)

_MISSING = object()  # 区分"本轮未找到"与找到的空值(如空字符串文本)

logger = logging.getLogger("mobile_auto.dialog_guard")


def _explicit_str(value: Any, field_name: str) -> str | None:
    """显式传入的包名/activity 类型校验;空串归一为 None 表示"未给出"。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field_name} 必须是非空字符串或空")
    return value


class _TemplateCache:
    """资产解析缓存(P2):键 (资产根, 逻辑名),清单 version_id 变化即失效。

    命中依赖资产层不变量:版本目录不可变,同一 version_id 的磁盘内容一经
    load 校验通过即不再变化;更新经清单原子切换产生新 version_id。因此
    命中路径跳过的只是重复的读盘、摘要与解码,不弱化完整性保证。
    """

    def __init__(self, capacity: int = 32) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: OrderedDict[
            tuple[str, str],
            tuple[str, object, object],
        ] = OrderedDict()

    def get(self, store, logical: str):
        """返回 (LoadedTemplate, PreparedTemplate);未命中走完整 load 校验。"""
        from ..imaging.engine import prepare_template

        key = (str(store.root), logical)
        version_id = store.list_templates().get(logical, {}).get("version_id")
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] == version_id:
                self._entries.move_to_end(key)
                return hit[1], hit[2]
        loaded = store.load(logical)
        prepared = prepare_template(loaded.png, what=f"模板 {logical}")
        with self._lock:
            self._entries[key] = (loaded.version_id, loaded, prepared)
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return loaded, prepared


class MobileAdapter:
    """统一驱动的异步方法面;platform 固定 android。"""

    platform = "android"

    def __init__(self, session: DeviceSession) -> None:
        self._session = session
        self._templates = _TemplateCache()
        self._device_ops = None

    @property
    def device(self):
        """平台扩展面(契约外命名空间):坐标手势原语 tap/swipe/long_press。

        15 方法契约不映射此处,契约测试不涉及(2026-10-24 契约扩展决定);
        语义与通道选择见 core/device_ops.py 模块注释。
        """
        if self._device_ops is None:
            from .device_ops import DeviceOps

            self._device_ops = DeviceOps(self)
        return self._device_ops

    async def _call(self, operation: Callable[[Any], T]) -> T:
        """提交到会话队列并等待;对未开始任务的取消传播由 wrap_future 提供。

        等待方被取消时向会话登记:排队未开始的任务取消;已运行的操作
        结果记为 unknown,不自动重放(plan 7.1)。
        """
        future: Future[T] = self._session.submit(operation)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            self._session.mark_cancelled(future)
            raise

    def _locate(self, backend, target: Target) -> list:
        """单轮定位:校验定位方式 → 底层查找 → 可见性过滤,保持树遍历序。"""
        validate_locatable(target, NATIVE_LOCATORS)
        return visible_only(
            backend.find_elements(target.by, target.value),
            backend.screen_size(),
        )

    def _store(self):
        from ..imaging.store import TemplateStore

        return TemplateStore(self._session.config.assets_dir)

    def _image_match(self, backend, target: Target):
        """IMAGE 目标的单轮采集与匹配;返回 (模板元数据, 匹配结果列表)。

        模板经会话级缓存按版本解析(P2):同版本命中免重复读盘与解码,
        清单切换即失效。匹配无结果是空列表——这是查询结果,不是错误;
        非法模板与设备错误分别以 InvalidTemplate / DeviceUnavailable 报告。
        """
        from ..imaging.engine import match_prepared

        template, prepared = self._templates.get(self._store(), target.value)
        results = match_prepared(
            self._session.screenshot_bytes(backend),
            prepared,
            **image_match_kwargs(template.meta, target),
        )
        return template.meta, results

    def _reject_image(self, target: Target, operation: str) -> None:
        if target.by.value == "image":
            raise UnsupportedOperation(
                f"IMAGE 目标不支持 {operation};图像路径仅支持 click/is_visible/"
                "wait_for/find_elements"
            )

    def _image_click_transaction(self, backend, target: Target) -> None:
        """图像点击事务:采集→匹配→选择→检查点→发送,同一队列任务内完成,
        其他设备请求无法插队(plan 6.2)。采集到发送超过 1 秒则重采集一次,
        仍过期则失败,不无限重试。计时含采集与匹配:耗时较长的匹配同样
        消耗新鲜度,不得把过期结果当新鲜结果点击。"""
        captured_at = time.monotonic()
        template_meta, results = self._image_match(backend, target)
        chosen = resolve_single(results, target)
        if time.monotonic() - captured_at > _IMAGE_FRESH_SECONDS:
            captured_at = time.monotonic()
            template_meta, results = self._image_match(backend, target)
            chosen = resolve_single(results, target)
            if time.monotonic() - captured_at > _IMAGE_FRESH_SECONDS:
                raise DeviceUnavailable(
                    "图像点击采集持续过期(重采集一次仍超时);请缩小搜索区域或提升阈值"
                )
        self._session.send_checkpoint()  # 必测窗口:匹配完成后、点击发送前
        self._session.inject_tap(backend, *chosen.center)

    async def _poll_until(self, attempt: Callable[[Any], T], target: Target) -> T:
        """在查找预算内轮询 attempt;歧义立即报,预算耗尽报 ElementNotFound。

        attempt 每轮返回结果或 _MISSING;AmbiguousTarget 等终止性错误直接
        传播,不重试。取消在轮询间隙生效,取消后不再发起新一轮查询。

        弹窗守卫(配置 dialog_guard 时):预算耗尽后在同一队列内按白名单
        清理一次,命中则重置一次预算继续轮询;未命中则按原语义失败,不
        额外等待。清理只发生在超时路径,不影响正常查询的轮询节奏。
        """
        config = self._session.config
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.wait_timeout
        dismissed_once = False
        dismissed: list[str] = []
        while True:
            result = await self._call(attempt)
            if result is not _MISSING:
                return result
            remaining = deadline - loop.time()
            if remaining <= 0:
                if not dismissed_once:
                    dismissed_once = True
                    dismissed = await self._dismiss_dialogs()
                    if dismissed:
                        deadline = loop.time() + config.wait_timeout
                        continue
                raise ElementNotFound(
                    f"{target.by.value}={target.value!r} 在 {config.wait_timeout} 秒内"
                    "未出现可见唯一匹配" + self._dialog_suffix(dismissed)
                )
            await asyncio.sleep(min(config.poll_interval, remaining))

    def _dismiss_dialogs_once(self, backend) -> list[str]:
        """设备队列线程内执行:按白名单查找并点击系统弹窗,返回命中目标值。

        白名单在配置层已强制 packageName 限定,不会命中业务页面的同名按钮;
        匹配不唯一(含 index 越界)时跳过,不猜测。发送前过取消检查点,
        与普通点击同一语义。
        """
        dismissed: list[str] = []
        screen = backend.screen_size()
        for target in self._session.config.dialog_guard:
            matches = visible_only(
                backend.find_elements(target.by, target.value), screen
            )
            if not matches:
                continue
            try:
                node = resolve_single(matches, target)
            except (ElementNotFound, AmbiguousTarget):
                logger.warning("弹窗白名单 %r 匹配不唯一,跳过不点", target.value)
                continue
            self._session.send_checkpoint()
            node.click()
            dismissed.append(target.value)
        return dismissed

    async def _dismiss_dialogs(self) -> list[str]:
        """预算耗尽时的一次性清理;未配置、会话故障或清理自身失败返回空,
        绝不掩盖等待路径的原始错误。"""
        if not self._session.config.dialog_guard:
            return []
        try:
            dismissed = await self._call(self._dismiss_dialogs_once)
        except Exception as exc:  # noqa: BLE001 - 清理失败不得掩盖原始超时
            logger.warning("系统弹窗清理失败(%s);按原错误继续", type(exc).__name__)
            return []
        self._session.note_dialog_guard_attempt(dismissed)
        return dismissed

    @staticmethod
    def _dialog_suffix(dismissed: list[str]) -> str:
        """失败信息里如实标注已尝试清理,避免被误读为静默重试。"""
        if not dismissed:
            return ""
        return (
            f";已尝试清理系统弹窗(命中 {len(dismissed)} 个:"
            f"{', '.join(dismissed)}),仍未见目标"
        )

    @journaled("goto")
    async def goto(
        self,
        target: str = "",
        package: str | None = None,
        activity: str | None = None,
    ) -> None:
        """空串冷启动 App(先停止再启动,起点确定);非空按 Deep Link 白名单
        校验后经参数化 ADB 调用打开。

        package/activity 为显式覆盖:给出时优先于配置的 app_package/
        app_activity(用于同一会话切换被启动的 App);两者都不给时沿用配置。
        只给 activity 不给 package 属配置错误——activity 的有无不能脱离包名谈。
        Deep Link 分支不使用这两个参数。

        白名单为空(默认)时任何链接都立即报配置错误(plan 5.3);
        不接受 shell 字符串,uri 作为独立参数传递。
        """
        if target:
            schemes = self._session.config.allowed_deeplink_schemes
            scheme = target.split(":", 1)[0].lower()
            if not schemes or scheme not in schemes:
                raise ConfigurationError(
                    f"Deep Link scheme {scheme!r} 不在 allowed_deeplink_schemes 白名单中"
                )
            await self._call(lambda backend: backend.open_deeplink(target))
            return
        config = self._session.config
        package = _explicit_str(package, "package")
        activity = _explicit_str(activity, "activity")
        if activity is not None and package is None:
            raise ConfigurationError("goto 显式传 activity 时必须同时传 package")
        package = package if package is not None else config.app_package
        activity = activity if activity is not None else config.app_activity
        if not package:
            raise ConfigurationError("未配置 app_package,无法启动 App")
        await self._call(
            lambda backend: backend.app_start(package, activity, stop=True)
        )

    @journaled("wait_for_load")
    async def wait_for_load(self) -> None:
        """仅等待配置的启动就绪目标可见(plan Q2);超时抛 WaitTimeout。

        业务页面就绪由调用方 wait_for 表达;缺配置立即报配置错误,
        不以固定 sleep 替代。预算沿用 wait_timeout。
        """
        ready_target = self._session.config.launch_ready_target
        if ready_target is None:
            raise ConfigurationError(
                "未配置 launch_ready_target,无法等待 App 启动就绪;"
                "启动就绪目标须显式配置"
            )
        config = self._session.config
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.wait_timeout
        dismissed_once = False
        dismissed: list[str] = []
        while True:
            if await self.is_visible(ready_target):
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                if not dismissed_once:
                    dismissed_once = True
                    dismissed = await self._dismiss_dialogs()
                    if dismissed:
                        deadline = loop.time() + config.wait_timeout
                        continue
                raise WaitTimeout(
                    f"启动就绪目标 {ready_target.by.value}={ready_target.value!r}"
                    f" 在 {config.wait_timeout} 秒内未可见"
                    + self._dialog_suffix(dismissed)
                )
            await asyncio.sleep(min(config.poll_interval, remaining))

    @journaled("back")
    async def back(self) -> None:
        """发送一次 Android 返回;不自动等待页面就绪。"""
        await self.press_key("back")

    @journaled("press_key")
    async def press_key(self, name: str) -> None:
        """发送有限键表内的按键;未知键报错,不开放任意 keycode。

        back/home 经双通道注入(按配置 injection_source,scrcpy 优先);
        其余键直接走 u2。
        """
        if name not in _PRESS_KEYS:
            raise InvalidTarget(
                f"不支持的按键:{name!r};支持:{', '.join(sorted(_PRESS_KEYS))}"
            )
        await self._call(
            lambda backend: self._session.inject_press_key(backend, name)
        )

    @journaled("scroll")
    async def scroll(self, direction: str, amount: int) -> None:
        """滚动:direction 为视口沿内容移动方向(down 对应手指向上)。

        amount 为正整数像素,夹限到屏幕安全区域;手势时长 0.3 秒,
        不承诺实际内容恰好移动同样像素。未知方向与非法距离报错。
        手势经双通道注入(按配置 injection_source,scrcpy 优先)。
        """
        if direction not in _SCROLL_DIRECTIONS:
            raise InvalidTarget(
                f"不支持的滚动方向:{direction!r};"
                f"支持:{', '.join(sorted(_SCROLL_DIRECTIONS))}"
            )
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise InvalidTarget("amount 必须是正整数像素")
        gesture_seconds = 0.3

        def operation(backend):
            start, end = scroll_points(backend.screen_size(), direction, amount)
            self._session.inject_swipe(
                backend, start[0], start[1], end[0], end[1], gesture_seconds
            )

        await self._call(operation)

    @journaled("input_text")
    async def input_text(self, target: Target, text: str) -> None:
        """清空并替换目标文本;不可编辑目标明确失败,不隐式追加。

        不读回文本做验证;输入内容不进日志。
        """
        self._reject_image(target, "input_text")

        def attempt(backend):
            try:
                node = resolve_single(self._locate(backend, target), target)
            except ElementNotFound:  # 本轮未出现:交给轮询继续
                return _MISSING
            ensure_editable(getattr(node, "info", {}) or {})
            self._session.send_checkpoint()  # 匹配完成、发送前的取消检查点
            node.clear_text()
            node.set_text(text)
            return True

        await self._poll_until(attempt, target)

    @journaled("get_attribute")
    async def get_attribute(self, target: Target, name: str) -> str | tuple[int, int, int, int]:
        """读取属性:未知属性立即报错;已知缺失返回空串;布尔为 true/false。"""
        self._reject_image(target, "get_attribute")
        validate_attribute_name(name)

        def attempt(backend):
            try:
                node = resolve_single(self._locate(backend, target), target)
            except ElementNotFound:
                return _MISSING
            return attribute_value(getattr(node, "info", {}) or {}, name)

        return await self._poll_until(attempt, target)

    @journaled("find_elements")
    async def find_elements(self, target: Target) -> list[ElementHandle]:
        """无序号返回全部可见匹配(树遍历序);有序号返回至多一个。
        句柄绑定本会话,重用前自动重校验;每个句柄的 Target 记录自己的
        序号,重校验按序号重定位、按身份确认,互不串位。IMAGE 目标为
        屏幕匹配结果。"""

        def per_result(offset: int) -> Target:
            # 无序号查询的每个结果需要独立身份;原 Target 是共享不可变值
            if target.index is None:
                return dataclasses.replace(target, index=offset)
            return target  # 有序号查询至多一个结果,序号即身份

        def operation(backend):
            if target.by.value == "image":
                meta, results = self._image_match(backend, target)
                screen = backend.screen_size()
                return [
                    make_image_handle(
                        per_result(offset), result, meta, screen, owner=self._session
                    )
                    for offset, result in enumerate(select_matches(results, target))
                ]
            matches = self._locate(backend, target)
            return [
                make_handle(
                    element,
                    per_result(offset),
                    owner=self._session,
                    match_count=len(matches),
                )
                for offset, element in enumerate(select_matches(matches, target))
            ]

        return await self._call(operation)

    @journaled("is_visible")
    async def is_visible(self, target: Target) -> bool:
        """单次查询,不进行长等待。IMAGE 目标为单轮采集匹配。"""

        def operation(backend):
            if target.by.value == "image":
                _, results = self._image_match(backend, target)
                return bool(select_matches(results, target))
            return bool(select_matches(self._locate(backend, target), target))

        return await self._call(operation)

    @journaled("wait_for")
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
        dismissed_once = False
        dismissed: list[str] = []
        while True:
            if await self.is_visible(target):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                if not dismissed_once:
                    dismissed_once = True
                    dismissed = await self._dismiss_dialogs()
                    if dismissed:
                        deadline = loop.time() + budget
                        continue
                if dismissed:
                    logger.info(
                        "wait_for %r 未出现;已清理系统弹窗 %s", target.value, dismissed
                    )
                return False
            await asyncio.sleep(min(self._session.config.poll_interval, remaining))

    @journaled("click")
    async def click(self, target: Target) -> None:
        """等待单目标可见(预算内轮询)后,在同一队列任务里发送一次点击。

        多匹配无序号立即报歧义;预算耗尽报 ElementNotFound;
        匹配完成后、点击发送前的取消检查点保证被取消时不发出点击。
        IMAGE 目标走单事务:新采集→匹配→选择→检查点→发送(无轮询,
        采集过期重采一次,仍过期失败)。
        """
        if target.by.value == "image":
            try:
                await self._call(
                    lambda backend: self._image_click_transaction(backend, target)
                )
            except ElementNotFound as exc:
                # 图像路径没有轮询预算:弹窗盖住模板时会立即无匹配,
                # 同样按"清理一次、命中才重试"处理。
                dismissed = await self._dismiss_dialogs()
                if not dismissed:
                    raise
                try:
                    await self._call(
                        lambda backend: self._image_click_transaction(backend, target)
                    )
                except ElementNotFound:
                    raise ElementNotFound(
                        f"{exc}{self._dialog_suffix(dismissed)}"
                    ) from None
            return

        def attempt(backend):
            try:
                element = resolve_single(self._locate(backend, target), target)
            except ElementNotFound:  # 本轮未出现:交给轮询继续
                return _MISSING
            self._session.send_checkpoint()  # 必测窗口:点击发送前取消
            element.click()
            return True

        await self._poll_until(attempt, target)

    @journaled("get_text")
    async def get_text(self, target: Target) -> str:
        """等待单目标可见后返回原生文本;已知缺失归一为空字符串。"""
        self._reject_image(target, "get_text")

        def attempt(backend):
            try:
                element = resolve_single(self._locate(backend, target), target)
            except ElementNotFound:
                return _MISSING
            return element.text or ""

        return await self._poll_until(attempt, target)

    @journaled("screenshot")
    async def screenshot(self) -> bytes:
        """新采集画面显式编码为 PNG;不返回缓存帧。

        通道按配置 screenshot_source 选择(auto=scrcpy 优先、故障回退 u2)。
        """
        return await self._call(lambda backend: self._session.screenshot_bytes(backend))

    @journaled("close")
    async def close(self) -> None:
        """幂等;仅结束本会话的调度,不停止 App。"""
        await asyncio.to_thread(self._session.close)
