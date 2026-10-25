"""设备档案端点单元测试(P2 工单 06):伪造 adb,验证缓存与门控。"""

import pytest

pytest.importorskip("fastapi", reason="viewer extra 未安装")


class FakeAdbDevice:
    def __init__(self, serial, *, fail=False):
        self.serial = serial
        self.fail = fail
        self.calls = []

    def shell(self, cmd):
        self.calls.append(cmd)
        if self.fail:
            raise RuntimeError("设备失联")
        props = {
            "getprop ro.product.brand": "Xiaomi",
            "getprop ro.product.model": "2201123C",
            "getprop ro.build.version.release": "14",
        }
        if cmd in props:
            return props[cmd]
        if cmd == "wm size":
            return "Physical size: 1080x2400\nOverride size: none\n"
        return ""


@pytest.fixture
def fake_adb(monkeypatch):
    import adbutils

    devices = {}

    def device(serial):
        return devices.setdefault(serial, FakeAdbDevice(serial))

    monkeypatch.setattr(adbutils.adb, "device", device)
    return devices


def test_info_parses_and_caches(client, fake_adb):
    http, _, _ = client
    first = http.get("/api/v1/devices/SERIAL1/info")
    assert first.status_code == 200
    body = first.json()
    assert body["brand"] == "Xiaomi"
    assert body["model"] == "2201123C"
    assert body["android_version"] == "14"
    assert body["resolution"] == "1080x2400"
    second = http.get("/api/v1/devices/SERIAL1/info")
    assert second.json()["model"] == "2201123C"
    assert len(fake_adb["SERIAL1"].calls) == 4  # 第二次命中缓存,不再探测


def test_unknown_serial_404(client, fake_adb):
    http, _, _ = client
    res = http.get("/api/v1/devices/NOPE/info")
    assert res.status_code == 404
    assert "NOPE" not in fake_adb  # 未触发任何 adb 调用


def test_probe_failure_returns_unknown_fields(client, fake_adb):
    fake_adb["SERIAL1"] = FakeAdbDevice("SERIAL1", fail=True)
    http, _, _ = client
    res = http.get("/api/v1/devices/SERIAL1/info")
    assert res.status_code == 200
    body = res.json()
    assert body["brand"] is None and body["resolution"] is None
