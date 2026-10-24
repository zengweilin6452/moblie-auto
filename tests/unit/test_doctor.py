"""doctor 的外部行为:设备状态解析、依赖缺失报告与退出码。

依赖版本与设备列表通过注入伪造,不调用真实 adb。
"""

import pytest

from mobile_auto import cli
from mobile_auto.config import load_config

ADB_DEVICES_OUTPUT = """\
* daemon not running; starting now at tcp:5037
List of devices attached
ABC123	device product:demo model:Demo
DEF456	unauthorized
GHI789	offline

"""


def make_versions(**versions):
    return lambda distribution: versions.get(distribution)


def full_versions(**extra):
    versions = {
        "uiautomator2": "3.2.0",
        "pillow": "11.0.0",
        "opencv-python-headless": "4.10.0",
        "numpy": "2.1.0",
        "fastapi": "0.115.0",
        "uvicorn": "0.30.0",
    }
    versions.update(extra)
    return make_versions(**versions)


def devices_from_output(output):
    return lambda path: cli.parse_adb_devices(output)


def run_report(*, versions=None, adb="/fake/adb.exe", devices=None, **kwargs):
    config = load_config(None, **kwargs)
    finder = adb if callable(adb) else (lambda: adb)
    return cli.run_doctor(
        config,
        None,
        version_of=versions or full_versions(),
        adb_finder=finder,
        device_lister=devices or devices_from_output(ADB_DEVICES_OUTPUT),
    )


def test_parse_adb_devices_distinguishes_states():
    entries = cli.parse_adb_devices(ADB_DEVICES_OUTPUT)
    assert [(e.serial, e.state) for e in entries] == [
        ("ABC123", "device"),
        ("DEF456", "unauthorized"),
        ("GHI789", "offline"),
    ]
    assert [e.authorized for e in entries] == [True, False, False]


def test_parse_adb_devices_empty_output():
    assert cli.parse_adb_devices("List of devices attached\n") == []


def test_list_adb_devices_issues_exactly_one_readonly_command():
    """doctor 对设备的唯一动作是只读的 adb devices;不写入、不安装、不初始化。"""
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return FakeCompleted(0, ADB_DEVICES_OUTPUT)

    entries = cli.list_adb_devices("/fake/adb.exe", runner=fake_runner)
    assert calls == [["/fake/adb.exe", "devices"]]
    assert [e.serial for e in entries] == ["ABC123", "DEF456", "GHI789"]


class FakeCompleted:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_list_adb_devices_nonzero_exit_raises_probe_error():
    def fake_runner(cmd, **kwargs):
        return FakeCompleted(1, "", stderr="adb: device offline")

    with pytest.raises(cli.AdbProbeError, match="退出码 1"):
        cli.list_adb_devices("/fake/adb.exe", runner=fake_runner)


def test_healthy_environment_passes_without_device_warnings(tmp_path):
    report = run_report()
    assert report.exit_code == 0
    assert report.adb_path == "/fake/adb.exe"
    counts = {title: len(g) for title, g in report.device_groups().items()}
    assert counts == {"已授权在线": 1, "未授权": 1, "离线": 1, "其他状态": 0}
    assert report.warnings == []
    rendered = report.render()
    assert "已授权在线: 1 台 — ABC123" in rendered


def test_only_unauthorized_device_warns_but_exits_zero(tmp_path):
    report = run_report(
        devices=devices_from_output("List of devices attached\nDEF456\tunauthorized\n")
    )
    assert report.exit_code == 0
    assert any("已授权" in w for w in report.warnings)


def test_no_device_is_warning_not_failure(tmp_path):
    report = run_report(devices=devices_from_output("List of devices attached\n"))
    assert report.exit_code == 0
    assert any("未发现任何设备" in w for w in report.warnings)


def test_missing_core_dependency_fails_and_lists_it(tmp_path):
    report = run_report(versions=make_versions(uiautomator2="3.2.0"))
    assert report.exit_code == 1
    assert any("Pillow" in p for p in report.problems)
    rendered = report.render()
    assert "Pillow" in rendered and "未安装" in rendered


def test_optional_dependencies_missing_only_warns(tmp_path):
    report = run_report(versions=make_versions(uiautomator2="3.2.0", pillow="11.0.0"))
    optional = [d for d in report.dependencies if not d.required]
    assert optional and all(d.version is None for d in optional)
    assert report.exit_code == 0
    assert all("可选" in w for w in report.warnings)


def test_missing_adb_fails(tmp_path):
    report = run_report(adb=None)
    assert report.exit_code == 1
    assert any("未找到 adb" in p for p in report.problems)


def test_adb_probe_failure_fails(tmp_path):
    def failing_lister(path):
        raise cli.AdbProbeError("无法执行 adb(超时)")

    report = run_report(devices=failing_lister)
    assert report.exit_code == 1
    assert report.adb_error is not None
    assert any("adb 探测失败" in p for p in report.problems)


def test_configured_serial_not_online_warns_but_does_not_autoselect(tmp_path):
    report = run_report(device_serial="MISSING99")
    assert report.exit_code == 0
    assert any("MISSING99" in w for w in report.warnings)
    # 不自动选择设备:报告只按状态列出,不存在"已选中设备"概念
    assert "已选中" not in report.render()


def test_other_device_states_counted_separately(tmp_path):
    report = run_report(
        devices=devices_from_output("List of devices attached\nRECOVERY1\trecovery\n")
    )
    assert report.exit_code == 0
    assert [d.serial for d in report.device_groups()["其他状态"]] == ["RECOVERY1"]
