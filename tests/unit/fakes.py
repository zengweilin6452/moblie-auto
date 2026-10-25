"""单元测试共用伪造:设备后端与元素,注入 DeviceSession/MobileAdapter。"""

import json

from mobile_auto import DeviceSession, MobileAdapter, MobileConfig


class FakeElement:
    """满足 BackendElement 形状的伪造节点;记录动作。"""

    def __init__(self, bounds, text="", info=None):
        self.bounds = bounds
        self.text = text
        self.info = dict(info or {})
        if "text" not in self.info:
            self.info["text"] = text
        self.click_calls = 0
        self.text_inputs = []

    def click(self) -> None:
        self.click_calls += 1

    def clear_text(self) -> None:
        self.text_inputs.append("")

    def set_text(self, text: str) -> None:
        self.text_inputs.append(text)


# u2 选择器字段 → info 键的映射(与真机 u2 info 键一致)
_SELECTOR_KEY_TO_INFO = {
    "resourceId": "resourceName",
    "text": "text",
    "description": "contentDescription",
    "className": "className",
    "packageName": "packageName",
    "clickable": "clickable",
    "enabled": "enabled",
    "selected": "selected",
    "checked": "checked",
}


class FakeBackend:
    """满足 DeviceBackend 形状的伪造后端;不做任何真实设备调用。"""

    def __init__(
        self,
        screen=(1080, 2400),
        elements=None,
        find_script=None,
        screenshots=None,
        ui_xml="",
        element_filter=None,
    ):
        self.screen = screen
        self.elements = elements or {}  # (Locator 值, value) -> [FakeElement]
        self.find_script = list(find_script or [])  # 每次查询依次弹出,耗尽后回落 elements
        self.screenshots = list(screenshots or [])
        self.ui_xml = ui_xml
        self.connect_calls = []
        self.app_starts = []
        self.deeplinks = []
        self.press_keys = []
        self.swipes = []
        self.taps = []
        self.long_clicks = []
        self.touch_ups = []
        self.find_calls = 0
        self.screenshot_calls = 0
        self.closed = False
        self.element_filter = element_filter  # (by, value, element) -> bool

    def connect(self, serial: str) -> None:
        self.connect_calls.append(serial)

    def close(self) -> None:
        self.closed = True

    def screen_size(self):
        return self.screen

    def find_elements(self, by, value):
        self.find_calls += 1
        if self.find_script:
            return self.find_script.pop(0)
        if self.element_filter is not None:
            return [el for el in self._all_elements() if self.element_filter(by, value, el)]
        if by.value == "u2_selector":
            # 谓词匹配:按 u2 选择器字段过滤全部元素
            return [el for el in self._all_elements() if self._matches_selector(value, el)]
        return list(self.elements.get((by.value, value), []))

    def _all_elements(self):
        seen = []
        for els in self.elements.values():
            for el in els:
                if el not in seen:
                    seen.append(el)
        return seen

    @staticmethod
    def _matches_selector(value, el) -> bool:
        predicate = json.loads(value)
        for key, expected in predicate.items():
            if el.info.get(_SELECTOR_KEY_TO_INFO[key]) != expected:
                return False
        return True

    def screenshot_png(self) -> bytes:
        self.screenshot_calls += 1
        if self.screenshots:
            return self.screenshots.pop(0)
        return f"PNG-{self.screenshot_calls}".encode()

    def app_start(self, package, activity, stop=False):
        self.app_starts.append((package, activity, stop))

    def open_deeplink(self, uri: str) -> None:
        self.deeplinks.append(uri)

    def press_key(self, name: str) -> None:
        self.press_keys.append(name)

    def swipe(self, sx, sy, ex, ey, duration):
        self.swipes.append((sx, sy, ex, ey, duration))

    def long_click(self, x, y, duration=0.8):
        self.long_clicks.append((x, y, duration))

    def touch_up(self, x, y):
        self.touch_ups.append((x, y))

    def tap(self, x, y):
        self.taps.append((x, y))

    def dump_ui(self) -> str:
        return self.ui_xml


class NullLease:
    """测试用租约:记录获取/释放,不做真实跨进程锁定。"""

    def __init__(self, serial):
        self.serial = serial
        self.acquired = 0
        self.released = 0

    def acquire(self) -> None:
        self.acquired += 1

    def release(self) -> None:
        self.released += 1


def make_session(
    config: MobileConfig | None = None,
    backend: FakeBackend | None = None,
    states: dict[str, str] | None = None,
    lease_factory=None,
    channel_factory=None,
):
    """构建注入伪造的会话;默认唯一已授权设备 SERIAL1 与空租约。"""
    config = config or MobileConfig(device_serial="SERIAL1")
    backend = backend or FakeBackend()
    if lease_factory is None:
        lease_factory = NullLease
    session = DeviceSession(
        config,
        backend_factory=lambda: backend,
        device_enumerator=lambda: {"SERIAL1": "device"} if states is None else states,
        lease_factory=lease_factory,
        # 单测密闭:视频通道始终启动失败(auto 策略即时回退 u2,不触碰 adb)
        channel_factory=channel_factory or no_channel_factory,
    )
    return session, backend


def no_channel_factory(serial):
    from mobile_auto.errors import StreamUnavailable

    raise StreamUnavailable("单测禁用视频通道")


class FakeChannel:
    """手势注入所需形状的伪造通道;按事件名脚本化失败。

    fail_on: {"down"|"move"|"up": Exception};事件触发时抛出,
    否则记录 (事件名, x, y)。
    """

    def __init__(self, fail_on: dict | None = None):
        self.events: list[tuple[str, int, int]] = []
        self.fail_on = fail_on or {}
        self.started = False

    def start(self, timeout: float = 0) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def healthy(self) -> bool:
        return self.started

    def _record(self, name: str, x: int, y: int) -> None:
        exc = self.fail_on.get(name)
        if exc is not None:
            raise exc
        self.events.append((name, x, y))

    def touch_down(self, x: int, y: int) -> None:
        self._record("down", x, y)

    def touch_move(self, x: int, y: int) -> None:
        self._record("move", x, y)

    def touch_up(self, x: int, y: int) -> None:
        self._record("up", x, y)

    def press_key(self, name: str) -> None:
        exc = self.fail_on.get(f"press:{name}")
        if exc is not None:
            raise exc
        self.events.append(("press", name, 0))


def make_adapter(
    config: MobileConfig | None = None,
    backend: FakeBackend | None = None,
    states: dict[str, str] | None = None,
):
    """构建已启动的会话与其适配器。"""
    session, backend = make_session(config, backend, states)
    session.start()
    return MobileAdapter(session), backend


SCREEN = (1080, 2400)
