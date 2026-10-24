"""单元测试共用伪造:设备后端与元素,注入 DeviceSession/MobileAdapter。"""


from mobile_auto import DeviceSession, MobileAdapter, MobileConfig


class FakeElement:
    """满足 BackendElement 形状的伪造节点;记录点击。"""

    def __init__(self, bounds, text=""):
        self.bounds = bounds
        self.text = text
        self.click_calls = 0

    def click(self) -> None:
        self.click_calls += 1


class FakeBackend:
    """满足 DeviceBackend 形状的伪造后端;不做任何真实设备调用。"""

    def __init__(
        self,
        screen=(1080, 2400),
        elements=None,
        find_script=None,
        screenshots=None,
    ):
        self.screen = screen
        self.elements = elements or {}  # (Locator 值, value) -> [FakeElement]
        self.find_script = list(find_script or [])  # 每次查询依次弹出,耗尽后回落 elements
        self.screenshots = list(screenshots or [])
        self.connect_calls = []
        self.app_starts = []
        self.find_calls = 0
        self.screenshot_calls = 0
        self.closed = False

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
        return list(self.elements.get((by.value, value), []))

    def screenshot_png(self) -> bytes:
        self.screenshot_calls += 1
        if self.screenshots:
            return self.screenshots.pop(0)
        return f"PNG-{self.screenshot_calls}".encode()

    def app_start(self, package, activity, stop=False):
        self.app_starts.append((package, activity, stop))


def make_session(
    config: MobileConfig | None = None,
    backend: FakeBackend | None = None,
    states: dict[str, str] | None = None,
):
    """构建注入伪造的会话;默认唯一已授权设备 SERIAL1。"""
    config = config or MobileConfig(device_serial="SERIAL1")
    backend = backend or FakeBackend()
    session = DeviceSession(
        config,
        backend_factory=lambda: backend,
        device_enumerator=lambda: {"SERIAL1": "device"} if states is None else states,
    )
    return session, backend


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


def visible_element(text=""):
    return FakeElement((0, 100, 500, 200), text=text)


