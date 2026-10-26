"""05:等待消失与点到消失原语(wait_gone / click_until_gone)。"""

import json
import math
from pathlib import Path

import pytest
from fakes import FakeBackend, FakeElement, make_adapter

from mobile_auto import (
    AmbiguousTarget,
    DeviceUnavailable,
    Locator,
    MobileConfig,
    Target,
)
from mobile_auto.evidence.journal import Journal


def small_config(tmp_path=None, **overrides):
    base = dict(
        device_serial="SERIAL1",
        app_package="com.demo",
        wait_timeout=0.2,
        poll_interval=0.01,
    )
    if tmp_path is not None:
        base["artifacts_dir"] = tmp_path
    base.update(overrides)
    return MobileConfig(**base)


def attach(adapter, tmp_path):
    session = adapter._session
    session.set_journal(Journal(tmp_path, session.session_id))
    return session


def journal_lines(tmp_path):
    path = Path(tmp_path) / "actions.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def target():
    return Target(Locator.TEXT, "弹窗")


def visible_backend(**kwargs):
    return FakeBackend(
        elements={("text", "弹窗"): [FakeElement((0, 0, 200, 80))]}, **kwargs
    )


async def test_wait_gone_true_when_target_already_absent():
    adapter, backend = make_adapter()
    assert await adapter.wait_gone(target()) is True
    assert backend.find_calls == 1


async def test_wait_gone_polls_until_target_disappears():
    element = FakeElement((0, 0, 200, 80))
    backend = FakeBackend(find_script=[[element], [element], []])
    adapter, _ = make_adapter(small_config(), backend=backend)
    assert await adapter.wait_gone(target(), timeout=0.5) is True
    assert backend.find_calls == 3


async def test_wait_gone_false_after_budget_exhausted():
    adapter, backend = make_adapter(small_config(), backend=visible_backend())
    assert await adapter.wait_gone(target(), timeout=0.12) is False
    assert backend.find_calls >= 2  # 预算内轮询,不是单次查询


async def test_wait_gone_zero_timeout_single_query():
    adapter, backend = make_adapter(small_config(), backend=visible_backend())
    assert await adapter.wait_gone(target(), timeout=0) is False
    assert backend.find_calls == 1
    backend.elements.clear()
    assert await adapter.wait_gone(target(), timeout=0) is True
    assert backend.find_calls == 2


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True])
async def test_wait_gone_rejects_invalid_timeouts(bad):
    adapter, _ = make_adapter()
    with pytest.raises(ValueError):
        await adapter.wait_gone(target(), timeout=bad)


async def test_wait_gone_propagates_device_errors_not_false():
    adapter, backend = make_adapter()

    def broken_find(by, value):
        raise DeviceUnavailable("设备掉线")

    backend.find_elements = broken_find
    with pytest.raises(DeviceUnavailable):
        await adapter.wait_gone(target(), timeout=0.1)


async def test_click_until_gone_clicks_until_target_disappears():
    first, second = FakeElement((0, 0, 10, 10)), FakeElement((0, 0, 10, 10))
    backend = FakeBackend(find_script=[[first], [second], []])
    adapter, _ = make_adapter(small_config(), backend=backend)
    assert (
        await adapter.click_until_gone(target(), timeout=0.5, interval=0.01) is True
    )
    assert first.click_calls == 1 and second.click_calls == 1
    assert backend.find_calls == 3


async def test_click_until_gone_false_when_never_gone():
    row = FakeElement((0, 0, 100, 50))
    adapter, backend = make_adapter(
        small_config(wait_timeout=0.3),
        backend=FakeBackend(elements={("text", "弹窗"): [row]}),
    )
    assert await adapter.click_until_gone(target(), interval=0.1) is False
    expected = math.ceil(0.3 / 0.1)
    assert abs(row.click_calls - expected) <= 1
    assert backend.find_calls == row.click_calls  # 每轮一次查找,不做双重采集


async def test_click_until_gone_propagates_ambiguous_target():
    rows = [FakeElement((0, 0, 10, 10)), FakeElement((0, 20, 10, 30))]
    adapter, backend = make_adapter(
        small_config(),
        backend=FakeBackend(elements={("text", "弹窗"): rows}),
    )
    with pytest.raises(AmbiguousTarget):
        await adapter.click_until_gone(target(), timeout=0.2, interval=0.01)
    assert [row.click_calls for row in rows] == [0, 0]
    assert backend.find_calls == 1  # 歧义立即报,不重试


async def test_click_until_gone_index_out_of_range_counts_as_gone():
    row = FakeElement((0, 0, 10, 10))
    adapter, _ = make_adapter(
        small_config(), backend=FakeBackend(elements={("text", "弹窗"): [row]})
    )
    assert await adapter.click_until_gone(Target(Locator.TEXT, "弹窗", index=3)) is True
    assert row.click_calls == 0


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True])
async def test_click_until_gone_rejects_invalid_timeouts(bad):
    adapter, _ = make_adapter()
    with pytest.raises(ValueError):
        await adapter.click_until_gone(target(), timeout=bad)


@pytest.mark.parametrize("bad", [0, -0.1, float("nan"), float("inf"), True])
async def test_click_until_gone_rejects_invalid_intervals(bad):
    adapter, _ = make_adapter()
    with pytest.raises(ValueError):
        await adapter.click_until_gone(target(), timeout=0.1, interval=bad)


async def test_click_until_gone_propagates_device_errors():
    adapter, backend = make_adapter()

    def broken_find(by, value):
        raise DeviceUnavailable("设备掉线")

    backend.find_elements = broken_find
    with pytest.raises(DeviceUnavailable):
        await adapter.click_until_gone(target(), timeout=0.1, interval=0.01)


class _ImageResult:
    def __init__(self, center):
        self.center = center


async def test_click_until_gone_image_clicks_then_gone(monkeypatch):
    import mobile_auto.core.adapter as adapter_module

    rounds = [[_ImageResult((5, 5))]]

    def fake_image_match(self, backend, target):
        return {}, rounds.pop(0) if rounds else []

    monkeypatch.setattr(adapter_module.MobileAdapter, "_image_match", fake_image_match)
    adapter, backend = make_adapter(small_config())
    assert (
        await adapter.click_until_gone(
            Target(Locator.IMAGE, "demo/target.png"), timeout=0.2, interval=0.01
        )
        is True
    )
    assert backend.taps == [(5, 5)]


async def test_budget_exhaustion_journals_not_found_single_line(tmp_path):
    row = FakeElement((0, 0, 100, 50))
    adapter, _ = make_adapter(
        small_config(tmp_path, wait_timeout=0.05),
        backend=FakeBackend(elements={("text", "弹窗"): [row]}),
    )
    attach(adapter, tmp_path)
    assert await adapter.wait_gone(target(), timeout=0.05) is False
    assert await adapter.click_until_gone(target(), timeout=0.05, interval=0.01) is False
    lines = journal_lines(tmp_path)
    assert [line["op"] for line in lines] == ["wait_gone", "click_until_gone"]
    assert [line["outcome"] for line in lines] == ["not_found", "not_found"]
