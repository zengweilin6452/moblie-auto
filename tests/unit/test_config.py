"""配置合并(显式参数 > 环境变量 > TOML > 默认值)与校验的外部行为。"""

import dataclasses
import textwrap
from pathlib import Path

import pytest

from mobile_auto import (
    ConfigurationError,
    Locator,
    Target,
    load_config,
)

ENV_NAMES = ("MOBILE_DEVICE_SERIAL", "MOBILE_APP_PACKAGE", "MOBILE_APP_ACTIVITY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_any_source():
    config = load_config()
    assert config.device_serial is None
    assert config.app_package is None
    assert config.app_activity is None
    assert config.wait_timeout == 10.0
    assert config.poll_interval == 0.5
    assert config.port == 8765
    assert config.allowed_deeplink_schemes == ()
    assert config.launch_ready_target is None
    assert config.assets_dir == Path("assets")


def write_config(tmp_path, content):
    config_file = tmp_path / "mobile-auto.toml"
    config_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return config_file


def test_toml_overrides_defaults(tmp_path):
    config_file = write_config(
        tmp_path,
        """
        device_serial = "TOML123"
        app_package = "com.demo.app"
        wait_timeout = 5
        allowed_deeplink_schemes = ["https", "demoapp"]

        [launch_ready_target]
        by = "resource_id"
        value = "com.demo.app:id/ready"
        """,
    )
    config = load_config(config_file)
    assert config.device_serial == "TOML123"
    assert config.app_package == "com.demo.app"
    assert config.wait_timeout == 5.0
    assert config.allowed_deeplink_schemes == ("https", "demoapp")
    assert config.launch_ready_target == Target(
        Locator.RESOURCE_ID, "com.demo.app:id/ready"
    )


def test_env_overrides_toml(tmp_path, monkeypatch):
    config_file = write_config(tmp_path, 'device_serial = "TOML123"\n')
    monkeypatch.setenv("MOBILE_DEVICE_SERIAL", "ENV456")
    assert load_config(config_file).device_serial == "ENV456"


def test_explicit_overrides_env_and_toml(tmp_path, monkeypatch):
    config_file = write_config(
        tmp_path,
        """
        device_serial = "TOML123"
        app_package = "com.demo.toml"
        """,
    )
    monkeypatch.setenv("MOBILE_DEVICE_SERIAL", "ENV456")
    monkeypatch.setenv("MOBILE_APP_PACKAGE", "com.demo.env")
    config = load_config(config_file, device_serial="EXPLICIT789")
    assert config.device_serial == "EXPLICIT789"
    assert config.app_package == "com.demo.env"


def test_empty_env_value_falls_back_to_toml(tmp_path, monkeypatch):
    config_file = write_config(tmp_path, 'device_serial = "TOML123"\n')
    monkeypatch.setenv("MOBILE_DEVICE_SERIAL", "   ")
    assert load_config(config_file).device_serial == "TOML123"


def test_relative_dirs_resolve_against_config_file_dir(tmp_path):
    config_file = tmp_path / "conf" / "mobile-auto.toml"
    config_file.parent.mkdir()
    config_file.write_text(
        'assets_dir = "assets"\nartifacts_dir = "C:/abs/artifacts"\n',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.assets_dir == (tmp_path / "conf" / "assets").resolve()
    assert config.artifacts_dir == Path("C:/abs/artifacts")


def test_relative_dirs_resolve_against_cwd_without_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(artifacts_dir="out")
    assert config.artifacts_dir == (tmp_path / "out").resolve()


def test_missing_config_file_is_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError, match="配置文件不存在"):
        load_config(tmp_path / "nope.toml")


def test_unknown_toml_key_is_configuration_error(tmp_path):
    config_file = write_config(tmp_path, "wait_time_out = 5\n")
    with pytest.raises(ConfigurationError, match="未知配置项"):
        load_config(config_file)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("wait_timeout = -1\n", "wait_timeout"),
        ("poll_interval = nan\n", "poll_interval"),
        ('wait_timeout = "十秒"\n', "wait_timeout"),
        ("port = 70000\n", "port"),
        ("port = true\n", "port"),
        ("device_serial = 123\n", "device_serial"),
        ('allowed_deeplink_schemes = ["https", ""]\n', "allowed_deeplink_schemes"),
        ('allowed_deeplink_schemes = "https"\n', "allowed_deeplink_schemes"),
        ('[launch_ready_target]\nby = "css"\nvalue = "#x"\n', "launch_ready_target"),
        (
            '[launch_ready_target]\nby = "resource_id"\nvalue = "x"\nindex = -2\n',
            "launch_ready_target",
        ),
    ],
)
def test_invalid_toml_values_report_configuration_error(tmp_path, content, match):
    config_file = write_config(tmp_path, content)
    with pytest.raises(ConfigurationError, match=match):
        load_config(config_file)


def test_explicit_invalid_value_reports_configuration_error():
    with pytest.raises(ConfigurationError, match="wait_timeout"):
        load_config(wait_timeout=0)
    with pytest.raises(ConfigurationError, match="port"):
        load_config(port=65536)


def test_config_is_immutable():
    config = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.wait_timeout = 99
