"""动作流水(01):字段、结局、脱敏、截断、重入保护与消费者。"""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fakes import FakeBackend, FakeElement, make_adapter, make_session

from mobile_auto import ElementNotFound, Locator, MobileConfig, Target
from mobile_auto.core.session import SessionState
from mobile_auto.evidence.collector import collect_failure_evidence
from mobile_auto.evidence.journal import Journal
from mobile_auto.pool import worker_artifacts_dir


def config(tmp_path, **overrides):
    base = dict(
        device_serial="SERIAL1",
        app_package="com.demo",
        artifacts_dir=tmp_path,
        wait_timeout=0.2,
        poll_interval=0.02,
    )
    base.update(overrides)
    return MobileConfig(**base)


def attach(adapter, tmp_path):
    session = adapter._session
    session.set_journal(Journal(tmp_path, session.session_id))
    return session


def lines(tmp_path):
    path = Path(tmp_path) / "actions.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def found_target():
    return Target(Locator.TEXT, "在")


def visible_backend(**kwargs):
    return FakeBackend(
        elements={("text", "在"): [FakeElement((0, 0, 9, 9))]}, **kwargs
    )


async def test_line_has_required_fields_and_only_target_keys(tmp_path):
    adapter, _ = make_adapter(config(tmp_path), backend=visible_backend())
    session = attach(adapter, tmp_path)
    target = Target(Locator.TEXT, "在", index=0, confidence=0.9)
    assert await adapter.is_visible(target) is True
    [line] = lines(tmp_path)
    assert line["op"] == "is_visible"
    assert line["session"] == session.session_id
    assert len(session.session_id) == 8
    assert line["outcome"] == "ok"
    assert line["duration_ms"] >= 0
    assert line["ts"].endswith("Z")
    assert line["target"] == {
        "by": "text",
        "value": "在",
        "index": 0,
        "confidence": 0.9,
    }
    assert "channel" not in line  # 读取类不臆造通道


async def test_outcome_ok_not_found_error_and_cancelled_before_send(tmp_path):
    adapter, _ = make_adapter(config(tmp_path), backend=visible_backend())
    attach(adapter, tmp_path)
    assert await adapter.wait_for(found_target(), timeout=0.05) is True
    assert await adapter.wait_for(Target(Locator.TEXT, "不在"), timeout=0.05) is False
    with pytest.raises(ElementNotFound):
        await adapter.click(Target(Locator.TEXT, "不在"))
    [ok, not_found, error] = lines(tmp_path)
    assert ok["outcome"] == "ok"
    assert not_found["outcome"] == "not_found"
    assert not_found["duration_ms"] >= 45  # 含完整轮询预算
    assert error["outcome"] == "error"
    assert error["error_type"] == "ElementNotFound"
    assert "message" not in error  # 不带异常消息正文

    cancel_adapter, _ = make_adapter(
        config(tmp_path / "cancel", wait_timeout=5.0, poll_interval=0.05)
    )
    attach(cancel_adapter, tmp_path / "cancel")
    task = asyncio.create_task(cancel_adapter.click(Target(Locator.TEXT, "永不出现")))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [cancelled] = lines(tmp_path / "cancel")
    # 轮询间隙取消:未进入发送阶段,属于明确失败而非结果未知
    assert cancelled["outcome"] == "error"
    assert cancelled["error_type"] == "CancelledError"


class _BlockingFindBackend(FakeBackend):
    """查找阻塞在发送检查点之前,用于确定"取消未进入发送阶段"。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def find_elements(self, by, value):
        self.entered.set()
        self.release.wait(5)
        return []


class _BlockingClickElement(FakeElement):
    """点击已过检查点后阻塞,用于确定"取消已进入发送阶段"。"""

    def __init__(self, bounds):
        super().__init__(bounds)
        self.entered = threading.Event()
        self.release = threading.Event()

    def click(self) -> None:
        self.entered.set()
        self.release.wait(5)
        super().click()


async def test_cancel_before_send_records_error_with_cancelled_type(tmp_path):
    backend = _BlockingFindBackend()
    adapter, _ = make_adapter(
        config(tmp_path, wait_timeout=5.0, poll_interval=0.05), backend=backend
    )
    attach(adapter, tmp_path)
    task = asyncio.create_task(adapter.click(Target(Locator.TEXT, "永恒")))
    assert await asyncio.to_thread(backend.entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    backend.release.set()
    [line] = lines(tmp_path)
    assert line["op"] == "click"
    assert line["outcome"] == "error"
    assert line["error_type"] == "CancelledError"


async def test_cancel_during_send_records_unknown(tmp_path):
    element = _BlockingClickElement((0, 0, 9, 9))
    adapter, _ = make_adapter(
        config(tmp_path),
        backend=FakeBackend(elements={("text", "在"): [element]}),
    )
    attach(adapter, tmp_path)
    task = asyncio.create_task(adapter.click(found_target()))
    assert await asyncio.to_thread(element.entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    element.release.set()
    [line] = lines(tmp_path)
    assert line["op"] == "click"
    assert line["outcome"] == "unknown"


async def test_input_text_records_length_not_text(tmp_path):
    box = FakeElement(
        (0, 0, 100, 40),
        info={"className": "android.widget.EditText", "text": ""},
    )
    adapter, _ = make_adapter(
        config(tmp_path), backend=FakeBackend(elements={("text", "输入"): [box]})
    )
    attach(adapter, tmp_path)
    await adapter.input_text(Target(Locator.TEXT, "输入"), "SECRET-PASSWORD-123")
    raw = (Path(tmp_path) / "actions.jsonl").read_text(encoding="utf-8")
    [line] = lines(tmp_path)
    assert line["op"] == "input_text"
    assert line["target"] == {"by": "text", "value": "输入"}
    assert line["text_length"] == len("SECRET-PASSWORD-123")
    assert "SECRET-PASSWORD-123" not in raw


async def test_get_text_does_not_record_return_value(tmp_path):
    node = FakeElement((0, 0, 100, 40), text="秘密文本")
    adapter, _ = make_adapter(
        config(tmp_path), backend=FakeBackend(elements={("text", "行"): [node]})
    )
    attach(adapter, tmp_path)
    assert await adapter.get_text(Target(Locator.TEXT, "行")) == "秘密文本"
    raw = (Path(tmp_path) / "actions.jsonl").read_text(encoding="utf-8")
    assert "秘密文本" not in raw


async def test_goto_records_only_scheme_or_cold_start(tmp_path):
    adapter, _ = make_adapter(
        config(tmp_path, allowed_deeplink_schemes=("demo",))
    )
    attach(adapter, tmp_path)
    await adapter.goto("demo://page/secret-token")
    await adapter.goto()
    deeplink, cold = lines(tmp_path)
    assert deeplink["scheme"] == "demo"
    assert cold["scheme"] == "cold_start"
    raw = (Path(tmp_path) / "actions.jsonl").read_text(encoding="utf-8")
    assert "secret-token" not in raw


async def test_injection_ops_record_channel_after_begin_operation(tmp_path):
    adapter, backend = make_adapter(config(tmp_path))
    session = attach(adapter, tmp_path)
    await adapter.back()
    session.begin_operation()
    assert session.last_injection_channel is None
    await adapter.device.tap(540, 1200)
    back, tap = lines(tmp_path)
    assert back["op"] == "back"  # 内部 press_key 不额外写行
    assert back["channel"] == "u2"
    assert tap["op"] == "device.tap"
    assert tap["channel"] == "u2"
    assert tap["point"] == [540, 1200]
    assert tap["normalized"] is False
    assert backend.taps == [(540, 1200)]


async def test_one_public_call_writes_one_line_for_wait_and_back(tmp_path):
    adapter, _ = make_adapter(
        config(tmp_path),
        backend=FakeBackend(
            find_script=[[], [], [FakeElement((0, 0, 9, 9))]],
            elements={("text", "在"): [FakeElement((0, 0, 9, 9))]},
        ),
    )
    attach(adapter, tmp_path)
    assert await adapter.wait_for(found_target(), timeout=0.2) is True
    await adapter.back()
    assert [line["op"] for line in lines(tmp_path)] == ["wait_for", "back"]


async def test_wait_for_load_writes_single_line(tmp_path):
    adapter, _ = make_adapter(
        config(tmp_path, launch_ready_target=Target(Locator.TEXT, "在")),
        backend=FakeBackend(elements={("text", "在"): [FakeElement((0, 0, 9, 9))]}),
    )
    attach(adapter, tmp_path)
    await adapter.wait_for_load()
    assert [line["op"] for line in lines(tmp_path)] == ["wait_for_load"]


def test_xdist_worker_keeps_file_in_worker_directory(tmp_path):
    base = tmp_path / "artifacts"
    journal = Journal(worker_artifacts_dir(base, "gw0"), "abcd1234")
    journal.record("click", duration_ms=1.0, outcome="ok")
    assert (base / "xdist-gw0" / "actions.jsonl").is_file()
    main = Journal(base, "abcd1234")
    main.record("click", duration_ms=1.0, outcome="ok")
    assert (base / "actions.jsonl").is_file()


def test_write_failure_never_changes_result(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    journal = Journal(blocked, "abcd1234")
    journal.record("click", duration_ms=1.0, outcome="ok")  # 不抛错
    assert journal.summary() is None


def test_write_failure_disables_journal_instead_of_retrying(tmp_path, caplog):
    """写失败一次即禁用:不再重试,告警与行为一致(directory-unwritable 同款)。"""

    class BrokenHandle:
        def __init__(self) -> None:
            self.calls = 0

        def fileno(self):
            self.calls += 1
            raise OSError("disk full")

        def close(self):
            pass

    journal = Journal(tmp_path, "abcd1234")
    handle = BrokenHandle()
    journal._handle = handle
    journal.record("click", duration_ms=1.0, outcome="ok")
    journal.record("click", duration_ms=1.0, outcome="ok")
    assert handle.calls == 1  # 第二次不再触碰坏句柄
    assert journal._disabled is True
    assert "已禁用" in caplog.text


async def test_write_failure_keeps_adapter_working(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    adapter, _ = make_adapter(config(tmp_path), backend=visible_backend())
    adapter._session.set_journal(Journal(blocked, "abcd1234"))
    assert await adapter.is_visible(found_target()) is True


def test_truncation_writes_marker_once(tmp_path):
    journal = Journal(tmp_path, "abcd1234", max_bytes=200)
    for index in range(20):
        journal.record(
            f"device.tap{index}",
            duration_ms=1.0,
            outcome="ok",
            extra={"point": [index, index]},
        )
    rows = lines(tmp_path)
    markers = [row for row in rows if row.get("op") == "journal"]
    assert len(markers) == 1
    assert markers[0]["outcome"] == "truncated"
    size = (Path(tmp_path) / "actions.jsonl").stat().st_size
    assert size <= 200 + len('{"op":"journal","outcome":"truncated"}\n')
    journal.record("click", duration_ms=1.0, outcome="ok")
    assert len(lines(tmp_path)) == len(rows)


async def test_meta_journal_summary_counts_steps_and_first_failure(tmp_path):
    adapter, _ = make_adapter(config(tmp_path), backend=visible_backend())
    session = attach(adapter, tmp_path)
    await adapter.is_visible(found_target())
    with pytest.raises(ElementNotFound):
        await adapter.click(Target(Locator.TEXT, "不在"))
    record = collect_failure_evidence(session, "失败")
    summary = record["journal_summary"]
    assert summary["steps"] == 2
    assert summary["total_duration_ms"] >= 0
    assert summary["first_failure"]["step"] == 2
    assert summary["first_failure"]["op"] == "click"
    assert summary["first_failure"]["outcome"] == "error"
    assert summary["first_failure"]["error_type"] == "ElementNotFound"
    meta = json.loads(
        (Path(tmp_path) / "evidence" / record["evidence_id"] / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["journal_summary"] == summary


async def test_no_journal_attached_writes_nothing(tmp_path):
    adapter, _ = make_adapter(config(tmp_path))
    await adapter.is_visible(found_target())
    assert not (Path(tmp_path) / "actions.jsonl").exists()


def test_journal_summary_missing_file_is_none(tmp_path):
    assert Journal(tmp_path, "abcd1234").summary() is None


def test_plugin_enables_journal_only_with_flag(tmp_path):
    from mobile_auto.evidence.pytest_plugin import _enable_journal

    session, _backend = make_session(config(tmp_path))
    session.start()
    assert _enable_journal(session, False) is None
    assert session.journal is None
    journal = _enable_journal(session, True)
    assert journal is not None
    assert journal.path == Path(tmp_path) / "actions.jsonl"
    assert not journal.path.exists()  # 懒创建:未记录不产生文件
    journal.record("click", duration_ms=1.0, outcome="ok")
    assert journal.path.exists()
    journal.close()


class _TreeTimeoutBackend(FakeBackend):
    def dump_ui(self):
        from uiautomator2.exceptions import HTTPTimeoutError

        raise HTTPTimeoutError("dump_hierarchy 超时")


def test_dump_timeout_attributed_and_session_not_faulted(tmp_path):
    session, _backend = make_session(config(tmp_path), backend=_TreeTimeoutBackend())
    session.start()
    record = collect_failure_evidence(session, "原始失败")
    assert record["not_captured"] == [
        {"kind": "post-failure", "reason": "UI 树取证未完成(HTTPTimeoutError)"}
    ]
    assert session.state is SessionState.LEASED  # 单次取证超时不得把会话打成 FAULTED
    assert record["device"]["screen_size"] is None
    kinds = {artifact["kind"] for artifact in record["artifacts"]}
    assert kinds == {"failure-frame"}


class _Done:
    def __init__(self, value):
        self._value = value

    def result(self, timeout=None):
        return self._value

    def cancel(self):
        return False


class _TimeoutResult:
    def __init__(self):
        self.timeouts = []

    def result(self, timeout=None):
        from uiautomator2.exceptions import HTTPTimeoutError

        self.timeouts.append(timeout)
        raise HTTPTimeoutError("dump_hierarchy 超时")

    def cancel(self):
        return False


class _BudgetSession:
    """最小会话面:截图立即完成,树取证按预算抛 HTTPTimeoutError。"""

    def __init__(self, tmp_path):
        self.config = MobileConfig(
            device_serial="SERIAL1", app_package="com.demo", artifacts_dir=tmp_path
        )
        self.serial = "SERIAL1"
        self.state = SessionState.LEASED
        self.dialog_guard_stats = {"attempts": 0, "dismissed": [], "last_at": None}
        self.tree_future = _TimeoutResult()
        self.calls = 0

    def healthy_channel(self):
        return None

    def submit(self, operation):
        self.calls += 1
        return _Done(b"PNG-POST") if self.calls == 1 else self.tree_future


def test_dump_timeout_waits_are_bounded_by_capture_budget(tmp_path):
    session = _BudgetSession(tmp_path)
    record = collect_failure_evidence(session, "失败")
    assert record["not_captured"][0]["reason"] == "UI 树取证未完成(HTTPTimeoutError)"
    assert session.tree_future.timeouts == [5.0]  # 默认取证预算 5s
    assert session.state is SessionState.LEASED


def test_begin_operation_clears_channel_snapshot(tmp_path):
    session, _backend = make_session(config(tmp_path))
    session.start()
    assert session.last_injection_channel is None
    session._note_injection_channel("scrcpy")
    assert session.last_injection_channel == "scrcpy"
    session.begin_operation()
    assert session.last_injection_channel is None


def test_journal_line_number_matches_public_call_count(tmp_path):
    adapter, _ = make_adapter(config(tmp_path), backend=visible_backend())
    attach(adapter, tmp_path)

    async def scenario():
        await adapter.goto()
        await adapter.is_visible(found_target())
        await adapter.click(found_target())
        await adapter.scroll("down", 300)
        await adapter.device.tap(10, 20)
        await adapter.back()

    asyncio.run(scenario())
    time.sleep(0.01)
    assert len(lines(tmp_path)) == 6
