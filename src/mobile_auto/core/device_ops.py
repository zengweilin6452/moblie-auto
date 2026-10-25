"""adapter.device 平台扩展面:坐标手势原语(15 方法契约之外)。

来源:深场景验证(2026-10-25)结论——坐标手势/长按/定向内层滚动不可经
契约 scroll/click 表达;按契约扩展决定(2026-10-24)以独立命名空间提供,
绑定层不映射、契约测试不涉及。

通道选择(source,按调用传参,不做全局配置):
- "auto"(默认):scrcpy 控制流优先(原始 socket 写入,毫秒级/op),通道
  不可用或首写(DOWN)失败时整个操作回退 u2(每 op 约 80ms RPC 往返,
  P1 截图双通道实测的传输差同源);
- "scrcpy":强制 scrcpy,不可用/故障如实报错,不静默换通道;
- "u2":强制 u2(时序敏感、要求可复现的用例)。

正确性不变量:手势是多写序列(DOWN…UP),DOWN 已发出后的任何失败都先
尽力补发 UP(scrcpy 失败时经 u2 touch_up 收尾,两者同经 InputManager
注入)再如实报错,不重放整个手势(避免双注入);整操作回退只发生在
DOWN 尚未发出之前。u2 侧 long_press 用 u2 原生 long_click(真机验证
有效),不用同点 swipe 拼(会被 App 识别为普通点击,深场景 S4 实证)。
"""

from __future__ import annotations

import time

from ..errors import InvalidTarget, StreamUnavailable

_SOURCES = ("auto", "scrcpy", "u2")
_MAX_SECONDS = 10.0  # 手势时长上限:阻塞会话队列线程的保护栏


class _CleanFallback(Exception):
    """DOWN 未发出即失败:操作整体可安全回退另一通道(携带原始错误)。"""

    def __init__(self, original: BaseException) -> None:
        super().__init__(original)
        self.original = original


def _validate_source(source: str) -> None:
    if source not in _SOURCES:
        raise InvalidTarget(f"source 必须是 {_SOURCES} 之一:{source!r}")


def _validate_xy(values: tuple) -> None:
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int):
            raise InvalidTarget(f"坐标必须是设备像素整数:{v!r}")


def _validate_seconds(name: str, value) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < value <= _MAX_SECONDS
    ):
        raise InvalidTarget(f"{name} 必须是 (0, {_MAX_SECONDS}] 秒:{value!r}")
    return float(value)


class DeviceOps:
    """平台专属手势面;操作经会话队列执行,租约/串行/取消语义与契约方法一致。"""

    def __init__(self, adapter) -> None:
        self._adapter = adapter

    async def tap(self, x: int, y: int, *, source: str = "auto") -> str:
        """单击坐标点;返回实际使用的通道("scrcpy" / "u2")。"""
        _validate_xy((x, y))
        _validate_source(source)

        def op(backend):
            if self._via_scrcpy(source, backend, lambda ch: self._tap(ch, backend, x, y)):
                return "scrcpy"
            backend.tap(x, y)
            return "u2"

        return await self._adapter._call(op)

    async def swipe(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        duration_s: float = 0.3,
        *,
        source: str = "auto",
    ) -> str:
        """直线滑动手势(坐标级,可定向屏幕任意区域——契约 scroll 固定
        屏幕中线,无法定向内层容器);返回实际使用的通道。"""
        _validate_xy((x0, y0, x1, y1))
        duration_s = _validate_seconds("duration_s", duration_s)
        _validate_source(source)

        def op(backend):
            def gesture(ch):
                self._swipe(ch, backend, x0, y0, x1, y1, duration_s)

            if self._via_scrcpy(source, backend, gesture):
                return "scrcpy"
            backend.swipe(x0, y0, x1, y1, duration_s)
            return "u2"

        return await self._adapter._call(op)

    async def long_press(
        self, x: int, y: int, hold_s: float = 0.8, *, source: str = "auto"
    ) -> str:
        """长按坐标点(框架级 DOWN-hold-UP);返回实际使用的通道。

        u2 回退用原生 long_click(2026-10-25 真机验证 MIUI 桌面长按触发
        编辑模式);同点 swipe 拼长按不可靠,不采用。
        """
        _validate_xy((x, y))
        hold_s = _validate_seconds("hold_s", hold_s)
        _validate_source(source)

        def op(backend):
            if self._via_scrcpy(source, backend, lambda ch: self._press(ch, backend, x, y, hold_s)):
                return "scrcpy"
            backend.long_click(x, y, duration=hold_s)
            return "u2"

        return await self._adapter._call(op)

    # ── 通道执行与手势安全 ───────────────────────────────────

    def _via_scrcpy(self, source: str, backend, perform) -> bool:
        """在 scrcpy 通道上执行 perform(channel);返回 False 表示走 u2。

        DOWN 前失败:auto 整体回退 / scrcpy 原样抛原始错误;
        DOWN 后失败:perform 内部已完成 UP 收尾,StreamUnavailable 原样传播。
        """
        channel = self._acquire_channel(source)
        if channel is None:
            return False
        try:
            perform(channel)
            return True
        except _CleanFallback as failure:
            if source == "scrcpy":
                raise failure.original from None
            return False

    def _acquire_channel(self, source: str):
        if source == "u2":
            return None
        try:
            return self._adapter._session.get_or_start_channel()
        except Exception:
            if source == "scrcpy":
                raise
            return None  # auto:通道不可用,回退 u2

    def _tap(self, channel, backend, x: int, y: int) -> None:
        try:
            channel.touch_down(x, y)
        except StreamUnavailable as exc:
            raise _CleanFallback(exc) from exc
        time.sleep(0.02)
        try:
            channel.touch_up(x, y)
        except StreamUnavailable:
            self._recover_up(backend, x, y)
            raise

    def _swipe(
        self, channel, backend, x0, y0, x1, y1, duration_s: float
    ) -> None:
        try:
            channel.touch_down(x0, y0)
        except StreamUnavailable as exc:
            raise _CleanFallback(exc) from exc
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / 5))
        delay = max(duration_s / steps, 0.001)
        last_x, last_y = x0, y0
        try:
            for i in range(1, steps + 1):
                last_x = x0 + (x1 - x0) * i // steps
                last_y = y0 + (y1 - y0) * i // steps
                channel.touch_move(last_x, last_y)
                time.sleep(delay)
            channel.touch_up(x1, y1)
        except StreamUnavailable:
            self._recover_up(backend, last_x, last_y)
            raise

    def _press(self, channel, backend, x: int, y: int, hold_s: float) -> None:
        try:
            channel.touch_down(x, y)
        except StreamUnavailable as exc:
            raise _CleanFallback(exc) from exc
        time.sleep(hold_s)
        try:
            channel.touch_up(x, y)
        except StreamUnavailable:
            self._recover_up(backend, x, y)
            raise

    def _recover_up(self, backend, x: int, y: int) -> None:
        """scrcpy 中断后的抬指收尾:经 u2 裸注入 ACTION_UP,尽力而为。"""
        try:
            backend.touch_up(x, y)
        except Exception:  # noqa: BLE001 - 收尾失败不掩盖原始错误
            pass
