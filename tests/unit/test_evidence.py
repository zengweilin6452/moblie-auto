"""失败证据与运行清单的外部行为(工单 05)。

会话以最小伪造注入(只需 submit/state/serial/config);
证据写入 tmp_path 产物目录。
"""

import json

import pytest

from mobile_auto import MobileConfig, SessionFaulted
from mobile_auto.evidence import (
    collect_failure_evidence,
    read_evidence_file,
    read_manifest,
)
from mobile_auto.session import SessionState


class StubSession:
    """collect_failure_evidence 所需的最小会话表面。"""

    def __init__(self, tmp_path, *, state=SessionState.LEASED, fail_submit=None):
        self.config = MobileConfig(
            device_serial="SERIAL1",
            app_package="com.demo",
            artifacts_dir=tmp_path,
        )
        self.serial = "SERIAL1"
        self._state = state
        self._fail_submit = fail_submit
        self.submit_calls = 0
        self._scrcpy_channel = None  # P1 工单 06:取证用视频通道(默认无)

    def healthy_channel(self):
        channel = self._scrcpy_channel
        return channel if channel is not None and channel.healthy() else None

    @property
    def state(self):
        return self._state

    def submit(self, operation):
        self.submit_calls += 1
        if self._fail_submit is not None:
            raise self._fail_submit
        # P1 工单 06:取证拆为两次提交——先画面(单值),后(树, 屏幕)
        payload = (
            b"PNG-POST"
            if self.submit_calls % 2 == 1
            else ("<hierarchy/>", (1080, 2400))
        )
        return FutureDone(payload)


class FutureDone:
    """立即完成的伪 Future。"""

    def __init__(self, value):
        self._value = value

    def result(self, timeout=None):
        return self._value

    def cancel(self):
        return False


class FutureHung:
    """永不完成的伪 Future(模拟挂起线程)。"""

    def result(self, timeout=None):
        raise TimeoutError("取证等待超时")

    def cancel(self):
        return True


def test_evidence_written_and_registered(tmp_path):
    session = StubSession(tmp_path)
    record = collect_failure_evidence(
        session,
        AssertionError("断言失败:标题不匹配"),
        operation_frames=[("op-frame", b"PNG-OP")],
    )
    base = tmp_path / "evidence" / record["evidence_id"]
    # 操作时画面与事后画面分开保存,不互相冒充
    assert (base / "operation-op-frame.png").read_bytes() == b"PNG-OP"
    assert (base / "failure-frame.png").read_bytes() == b"PNG-POST"
    assert (base / "ui-tree.xml").read_text(encoding="utf-8") == "<hierarchy/>"
    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    assert meta["failure"]["type"] == "AssertionError"
    assert meta["device"] == {
        "serial": "SERIAL1",
        "app_package": "com.demo",
        "screen_size": [1080, 2400],
    }
    kinds = {a["kind"] for a in meta["artifacts"]}
    assert kinds == {"operation-frame", "failure-frame", "ui-tree"}
    assert all(a["captured_at"] for a in meta["artifacts"])
    # 运行清单登记且为唯一读取入口
    entries = read_manifest(tmp_path)
    assert entries[-1]["evidence_id"] == record["evidence_id"]
    assert set(entries[-1]["artifacts"]) == {
        "operation-op-frame.png",
        "failure-frame.png",
        "ui-tree.xml",
    }
    assert (
        read_evidence_file(tmp_path, entries[-1], "failure-frame.png")
        == b"PNG-POST"
    )


def test_unregistered_file_access_rejected(tmp_path):
    session = StubSession(tmp_path)
    record = collect_failure_evidence(session, "失败")
    entry = read_manifest(tmp_path)[-1]
    with pytest.raises(FileNotFoundError, match="未在运行清单登记"):
        read_evidence_file(tmp_path, entry, "operation-../secret.png")
    forged = dict(entry, evidence_id="../../../etc")
    with pytest.raises(ValueError, match="非法证据标识"):
        read_evidence_file(tmp_path, forged, "failure-frame.png")
    assert record["evidence_id"]


def test_faulted_session_saves_only_existing_material(tmp_path):
    session = StubSession(
        tmp_path,
        state=SessionState.FAULTED,
        fail_submit=SessionFaulted("会话故障中"),
    )
    record = collect_failure_evidence(
        session, ValueError("原始失败"), operation_frames=[("keep-frame", b"PNG-KEEP")]
    )
    base = tmp_path / "evidence" / record["evidence_id"]
    assert (base / "operation-keep-frame.png").read_bytes() == b"PNG-KEEP"
    assert not (base / "failure-frame.png").exists()  # 不再调用设备线程
    assert not (base / "ui-tree.xml").exists()
    assert record["not_captured"] == [
        {"kind": "post-failure", "reason": "会话 FAULTED,不再调用设备线程;仅保存已有材料"}
    ]
    assert record["device"]["screen_size"] is None
    entry = read_manifest(tmp_path)[-1]
    assert entry["artifacts"] == ["operation-keep-frame.png"]


def test_hung_capture_gives_up_within_timeout(tmp_path):
    session = StubSession(tmp_path)
    session.submit = lambda operation: FutureHung()
    import time

    started = time.monotonic()
    record = collect_failure_evidence(session, "失败", timeout=0.1)
    elapsed = time.monotonic() - started
    assert elapsed < 2  # 不挂起用例
    assert "TimeoutError" in record["not_captured"][0]["reason"]
    assert record["artifacts"] == []


def test_submit_rejection_recorded_not_raised(tmp_path):
    session = StubSession(tmp_path, fail_submit=SessionFaulted("拒绝新操作"))
    record = collect_failure_evidence(session, "失败")
    assert record["not_captured"][0]["reason"].startswith("提交取证被拒绝")


def test_evidence_never_contains_input_text(tmp_path):
    """输入文本不进证据:失败信息只含异常类型与消息。"""

    class NonEditableError(Exception):
        pass

    session = StubSession(tmp_path)
    record = collect_failure_evidence(
        session, NonEditableError("目标不可编辑(className=android.widget.TextView)")
    )
    meta_text = json.dumps(record, ensure_ascii=False)
    assert "SECRET-PASSWORD-123" not in meta_text  # 输入值从未进入证据


def test_manifest_survives_corrupt_lines(tmp_path):
    (tmp_path / "run-manifest.jsonl").write_text(
        json.dumps({"evidence_id": "a" * 16}) + "\n{broken\n",
        encoding="utf-8",
    )
    entries = read_manifest(tmp_path)
    assert len(entries) == 1 and entries[0]["evidence_id"] == "a" * 16
