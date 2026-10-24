"""失败证据视频帧路径单元测试(工单 06):scrcpy 帧优先、u2 回退、归因。"""

import io
import json

import cv2
import numpy as np
from fakes import FakeBackend, make_session

from mobile_auto import MobileConfig
from mobile_auto.evidence import collect_failure_evidence
from mobile_auto.session import SessionState


class FakeChannel:
    def __init__(self, *, fail=False, healthy=True):
        self.fail = fail
        self._healthy = healthy

    def healthy(self):
        return self._healthy

    def latest_frame(self):
        if self.fail:
            raise RuntimeError("取帧失败")
        return _jpeg_bytes(), 150, 200

    def stop(self):
        self._healthy = False


def _jpeg_bytes() -> bytes:
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(
        ".jpg", np.full((8, 8, 3), 200, dtype=np.uint8)
    )
    assert ok
    return buf.tobytes()


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image_new = __import__("PIL.Image", fromlist=["Image"]).new(
        "RGB", (8, 8), (200, 10, 10)
    )
    Image_new.save(buffer, format="PNG")
    return buffer.getvalue()


def make_session_with(config, png):
    session, backend = make_session(config=config, backend=FakeBackend(screenshots=[png]))
    session.start()
    return session


def evidence_dir(tmp_path, record):
    return tmp_path / "art" / "evidence" / record["evidence_id"]


def test_video_frame_preferred(tmp_path):
    session = make_session_with(
        MobileConfig(device_serial="SERIAL1", artifacts_dir=tmp_path / "art"),
        _png_bytes(),
    )
    session._scrcpy_channel = FakeChannel()
    try:
        record = collect_failure_evidence(session, "boom", timeout=2.0)
        assert record["frame_source"]["source"] == "scrcpy-video"
        assert record["frame_source"]["capture_lag_ms"] >= 0
        names = [a["name"] for a in record["artifacts"]]
        assert "failure-frame.png" in names and "ui-tree.xml" in names
        meta = json.loads(
            evidence_dir(tmp_path, record).joinpath("meta.json").read_text(encoding="utf-8")
        )
        assert meta["frame_source"]["source"] == "scrcpy-video"
        png = evidence_dir(tmp_path, record).joinpath("failure-frame.png").read_bytes()
        assert cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR) is not None
    finally:
        session.close()


def test_fallback_to_u2_when_channel_frame_fails(tmp_path):
    session = make_session_with(
        MobileConfig(device_serial="SERIAL1", artifacts_dir=tmp_path / "art"),
        _png_bytes(),
    )
    session._scrcpy_channel = FakeChannel(fail=True)  # 健康但取帧失败
    try:
        record = collect_failure_evidence(session, "boom", timeout=2.0)
        assert record["frame_source"]["source"] == "u2"
        assert record["not_captured"] == []
    finally:
        session.close()


def test_no_channel_uses_u2_and_annotates(tmp_path):
    session = make_session_with(
        MobileConfig(device_serial="SERIAL1", artifacts_dir=tmp_path / "art"),
        _png_bytes(),
    )
    try:
        record = collect_failure_evidence(session, "boom", timeout=2.0)
        assert record["frame_source"]["source"] == "u2"
    finally:
        session.close()


def test_faulted_session_records_reason(tmp_path):
    session = make_session_with(
        MobileConfig(device_serial="SERIAL1", artifacts_dir=tmp_path / "art"),
        _png_bytes(),
    )
    session._state = SessionState.FAULTED
    try:
        record = collect_failure_evidence(session, "boom", timeout=2.0)
        assert record["frame_source"] is None
        assert any(m["kind"] == "post-failure" for m in record["not_captured"])
    finally:
        session._state = SessionState.LEASED
        session.close()
