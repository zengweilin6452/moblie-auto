"""系统弹窗守卫:等待预算耗尽时同队列清理一次,命中才重置一次预算。

覆盖:原生点击/等待/启动就绪/图像点击四条路径,歧义不点、未命中不延长
预算、is_visible 不受影响、配置必须按 packageName 限定。
"""

import textwrap

import pytest
from fakes import FakeBackend, FakeElement, make_adapter

from mobile_auto import (
    ConfigurationError,
    ElementNotFound,
    Locator,
    MobileConfig,
    Target,
    load_config,
)

SERIAL = "SERIAL1"
DIALOG_SELECTOR = '{"packageName":"com.lbe.security.miui","text":"拒绝"}'
DIALOG = Target(Locator.U2_SELECTOR, DIALOG_SELECTOR)
TARGET = Target(Locator.RESOURCE_ID, "com.demo:id/ok")


class DialogElement(FakeElement):
    """弹窗按钮:点击后让被遮挡的目标出现,模拟"清理即解锁"。"""

    def __init__(self, backend):
        super().__init__(
            (500, 1000, 700, 1100),
            text="拒绝",
            info={
                "packageName": "com.lbe.security.miui",
                "text": "拒绝",
                "className": "android.widget.Button",
            },
        )
        self._backend = backend

    def click(self) -> None:
        super().click()
        self._backend.target_visible = True


class DialogBackend(FakeBackend):
    """按"弹窗未点 → 目标不可见;弹窗被点 → 目标可见"建模。"""

    def __init__(self):
        super().__init__()
        self.target_visible = False
        self.dialog_elements: list[DialogElement] = []
        self.ok = FakeElement((100, 100, 300, 200), text="OK")

    def find_elements(self, by, value):
        self.find_calls += 1
        if value == DIALOG_SELECTOR:
            return list(self.dialog_elements)
        if value == TARGET.value and self.target_visible:
            return [self.ok]
        return []


def backend_with_dialogs(count: int = 1) -> DialogBackend:
    backend = DialogBackend()
    backend.dialog_elements = [DialogElement(backend) for _ in range(count)]
    return backend


def guard_config(**overrides) -> MobileConfig:
    values = {
        "device_serial": SERIAL,
        "wait_timeout": 0.2,
        "poll_interval": 0.02,
        "dialog_guard": (DIALOG,),
    }
    values.update(overrides)
    return MobileConfig(**values)


async def test_click_dismisses_dialog_and_retries_once():
    backend = backend_with_dialogs()
    adapter, _ = make_adapter(config=guard_config(), backend=backend)

    await adapter.click(TARGET)

    assert [el.click_calls for el in backend.dialog_elements] == [1]
    assert backend.ok.click_calls == 1
    stats = adapter._session.dialog_guard_stats
    assert stats["attempts"] == 1
    assert stats["dismissed"] == [DIALOG_SELECTOR]
    assert stats["last_at"] is not None


async def test_timeout_without_dialog_does_not_extend_budget():
    backend = backend_with_dialogs(count=0)
    adapter, _ = make_adapter(config=guard_config(), backend=backend)

    with pytest.raises(ElementNotFound) as info:
        await adapter.click(TARGET)

    assert "已尝试清理系统弹窗" not in str(info.value)
    stats = adapter._session.dialog_guard_stats
    assert stats["attempts"] == 1 and stats["dismissed"] == []


async def test_wait_for_retries_after_dismissal():
    backend = backend_with_dialogs()
    adapter, _ = make_adapter(config=guard_config(), backend=backend)

    assert await adapter.wait_for(TARGET, timeout=0.2) is True
    assert backend.dialog_elements[0].click_calls == 1
    assert adapter._session.dialog_guard_stats["attempts"] == 1


async def test_wait_for_missing_returns_false_after_single_attempt():
    backend = backend_with_dialogs(count=0)
    adapter, _ = make_adapter(config=guard_config(), backend=backend)

    assert await adapter.wait_for(TARGET, timeout=0.05) is False
    assert adapter._session.dialog_guard_stats["attempts"] == 1


async def test_wait_for_load_retries_after_dismissal():
    config = guard_config(launch_ready_target=TARGET)
    backend = backend_with_dialogs()
    adapter, _ = make_adapter(config=config, backend=backend)

    await adapter.wait_for_load()

    assert backend.dialog_elements[0].click_calls == 1


async def test_ambiguous_dialog_is_skipped_not_guessed():
    backend = backend_with_dialogs(count=2)
    adapter, _ = make_adapter(config=guard_config(), backend=backend)

    with pytest.raises(ElementNotFound) as info:
        await adapter.click(TARGET)

    assert [el.click_calls for el in backend.dialog_elements] == [0, 0]
    assert "已尝试清理系统弹窗" not in str(info.value)
    stats = adapter._session.dialog_guard_stats
    assert stats["attempts"] == 1 and stats["dismissed"] == []


async def test_is_visible_never_dismisses():
    backend = backend_with_dialogs()
    adapter, _ = make_adapter(config=guard_config(), backend=backend)

    assert await adapter.is_visible(TARGET) is False

    assert backend.dialog_elements[0].click_calls == 0
    assert adapter._session.dialog_guard_stats["attempts"] == 0


async def test_guard_disabled_by_default():
    backend = backend_with_dialogs()
    config = MobileConfig(
        device_serial=SERIAL, wait_timeout=0.05, poll_interval=0.02
    )
    adapter, _ = make_adapter(config=config, backend=backend)

    with pytest.raises(ElementNotFound):
        await adapter.click(TARGET)

    assert backend.dialog_elements[0].click_calls == 0
    assert adapter._session.dialog_guard_stats["attempts"] == 0


async def test_image_click_retries_once_after_dismissal(monkeypatch):
    backend = backend_with_dialogs()
    adapter, _ = make_adapter(config=guard_config(), backend=backend)
    calls = {"n": 0}

    def fake_transaction(worker_backend, target):
        calls["n"] += 1
        if calls["n"] == 1:  # 首次:弹窗盖住模板,无匹配
            raise ElementNotFound("模板未见")

    monkeypatch.setattr(adapter, "_image_click_transaction", fake_transaction)

    await adapter.click(Target(Locator.IMAGE, "recorded/ok.png"))

    assert calls["n"] == 2
    assert backend.dialog_elements[0].click_calls == 1


async def test_image_click_without_dialog_raises_after_one_attempt(monkeypatch):
    backend = backend_with_dialogs(count=0)
    adapter, _ = make_adapter(config=guard_config(), backend=backend)
    calls = {"n": 0}

    def fake_transaction(worker_backend, target):
        calls["n"] += 1
        raise ElementNotFound("模板未见")

    monkeypatch.setattr(adapter, "_image_click_transaction", fake_transaction)

    with pytest.raises(ElementNotFound):
        await adapter.click(Target(Locator.IMAGE, "recorded/ok.png"))

    assert calls["n"] == 1


def test_dialog_guard_requires_package_scoped_u2_selector():
    with pytest.raises(ConfigurationError):
        MobileConfig(dialog_guard=(Target(Locator.TEXT, "拒绝"),))
    with pytest.raises(ConfigurationError):
        MobileConfig(
            dialog_guard=(Target(Locator.U2_SELECTOR, '{"text":"拒绝"}'),)
        )
    with pytest.raises(ConfigurationError):
        MobileConfig(dialog_guard=("not-a-target",))


def test_dialog_guard_defaults_to_empty():
    assert MobileConfig().dialog_guard == ()


def test_dialog_guard_from_toml(tmp_path):
    path = tmp_path / "mobile-auto.toml"
    path.write_text(
        textwrap.dedent(
            """
            [[dialog_guard]]
            by = "u2_selector"
            value = '{"packageName":"com.lbe.security.miui","text":"拒绝"}'
            """
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.dialog_guard == (DIALOG,)
