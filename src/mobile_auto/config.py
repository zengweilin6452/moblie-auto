"""配置定义、合并与校验(plan 9.1)。

优先级:显式构造参数 > 环境变量 > TOML > 默认值。
只读取三个沿用内网名称的环境变量(MOBILE_DEVICE_SERIAL /
MOBILE_APP_PACKAGE / MOBILE_APP_ACTIVITY),不新增环境变量。

TOML 为可选的单一配置文件,路径由调用方(如 CLI --config)指定;
键与字段同名。assets_dir / artifacts_dir 相对路径基于配置文件所在目录解析,
未指定配置文件时基于工作目录;load_config 返回的两者均为绝对路径。
launch_ready_target 用 TOML 表格表示
(by/value,可选 index、confidence、region),by 取 Locator 的小写字符串值。
"""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .contracts import Target
from .errors import ConfigurationError, InvalidTarget

ENV_DEVICE_SERIAL = "MOBILE_DEVICE_SERIAL"
ENV_APP_PACKAGE = "MOBILE_APP_PACKAGE"
ENV_APP_ACTIVITY = "MOBILE_APP_ACTIVITY"

_TOML_FIELDS = frozenset(
    {
        "device_serial",
        "app_package",
        "app_activity",
        "assets_dir",
        "artifacts_dir",
        "launch_ready_target",
        "allowed_deeplink_schemes",
        "wait_timeout",
        "poll_interval",
        "port",
        "screenshot_source",
        "injection_source",
    }
)

_SCREENSHOT_SOURCES = frozenset({"auto", "u2", "scrcpy"})
_INJECTION_SOURCES = _SCREENSHOT_SOURCES
_ENV_FIELDS = {
    "device_serial": ENV_DEVICE_SERIAL,
    "app_package": ENV_APP_PACKAGE,
    "app_activity": ENV_APP_ACTIVITY,
}

_UNSET: Any = object()


def _invalid(field_name: str, reason: str) -> ConfigurationError:
    return ConfigurationError(f"配置项 {field_name} 非法:{reason}")


@dataclass(frozen=True)
class MobileConfig:
    """合并后的运行配置;字段校验在构造时完成,非法值报 ConfigurationError。"""

    device_serial: str | None = None
    app_package: str | None = None
    app_activity: str | None = None
    assets_dir: Path = Path("assets")
    artifacts_dir: Path = Path("artifacts")
    launch_ready_target: Target | None = None
    allowed_deeplink_schemes: tuple[str, ...] = ()
    wait_timeout: float = 10.0
    poll_interval: float = 0.5
    port: int = 8765
    # 驱动截图通道:auto=scrcpy 优先、故障回退 u2;u2=强制 u2;scrcpy=强制且不回退
    screenshot_source: str = "auto"
    # 注入通道(scroll/图像点击发送/press_key back/home 与 adapter.device):
    # 同一语义;u2 可钉死时序敏感用例(手势插值 u2 在设备侧、scrcpy 在主机侧)
    injection_source: str = "auto"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "device_serial", _optional_str(self.device_serial, "device_serial")
        )
        object.__setattr__(self, "app_package", _optional_str(self.app_package, "app_package"))
        object.__setattr__(self, "app_activity", _optional_str(self.app_activity, "app_activity"))
        object.__setattr__(self, "assets_dir", _directory(self.assets_dir, "assets_dir"))
        object.__setattr__(self, "artifacts_dir", _directory(self.artifacts_dir, "artifacts_dir"))
        if self.launch_ready_target is not None and not isinstance(
            self.launch_ready_target, Target
        ):
            raise _invalid("launch_ready_target", "必须是 Target 或空")
        object.__setattr__(
            self,
            "allowed_deeplink_schemes",
            _schemes(self.allowed_deeplink_schemes, "allowed_deeplink_schemes"),
        )
        object.__setattr__(
            self, "wait_timeout", _positive_float(self.wait_timeout, "wait_timeout")
        )
        object.__setattr__(
            self, "poll_interval", _positive_float(self.poll_interval, "poll_interval")
        )
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise _invalid("port", "必须是 1-65535 之间的整数")
        if self.screenshot_source not in _SCREENSHOT_SOURCES:
            raise _invalid(
                "screenshot_source",
                f"必须是 {' / '.join(sorted(_SCREENSHOT_SOURCES))} 之一",
            )
        if self.injection_source not in _INJECTION_SOURCES:
            raise _invalid(
                "injection_source",
                f"必须是 {' / '.join(sorted(_INJECTION_SOURCES))} 之一",
            )


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(field_name, "必须是字符串或空")
    return value.strip() or None


def _directory(value: Any, field_name: str) -> Path:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise _invalid(field_name, "不能是空字符串")
        value = Path(value)
    if not isinstance(value, Path):
        raise _invalid(field_name, "必须是路径")
    return value


def _schemes(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise _invalid(field_name, "必须是字符串列表")
    schemes = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _invalid(field_name, "每一项都必须是非空字符串")
        schemes.append(item.strip())
    return tuple(schemes)


def _positive_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(field_name, "必须是正的有限数值")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise _invalid(field_name, "必须是正的有限数值")
    return number


def load_config(
    config_path: str | Path | None = None,
    *,
    device_serial: Any = _UNSET,
    app_package: Any = _UNSET,
    app_activity: Any = _UNSET,
    assets_dir: Any = _UNSET,
    artifacts_dir: Any = _UNSET,
    launch_ready_target: Any = _UNSET,
    allowed_deeplink_schemes: Any = _UNSET,
    wait_timeout: Any = _UNSET,
    poll_interval: Any = _UNSET,
    port: Any = _UNSET,
    screenshot_source: Any = _UNSET,
    injection_source: Any = _UNSET,
) -> MobileConfig:
    """按 显式参数 > 环境变量 > TOML > 默认值 的优先级合并配置并校验。"""
    toml_data = _read_toml(config_path)
    explicit = {
        "device_serial": device_serial,
        "app_package": app_package,
        "app_activity": app_activity,
        "assets_dir": assets_dir,
        "artifacts_dir": artifacts_dir,
        "launch_ready_target": launch_ready_target,
        "allowed_deeplink_schemes": allowed_deeplink_schemes,
        "wait_timeout": wait_timeout,
        "poll_interval": poll_interval,
        "port": port,
        "screenshot_source": screenshot_source,
        "injection_source": injection_source,
    }

    merged: dict[str, Any] = {}
    for name in _TOML_FIELDS:
        explicit_value = explicit[name]
        if explicit_value is not _UNSET:
            merged[name] = explicit_value
            continue
        env_name = _ENV_FIELDS.get(name)
        if env_name:
            env_value = os.environ.get(env_name, "").strip()
            if env_value:
                merged[name] = env_value
                continue
        if name in toml_data:
            merged[name] = toml_data[name]

    if isinstance(merged.get("launch_ready_target"), dict):
        merged["launch_ready_target"] = _target_from_toml(
            merged["launch_ready_target"], "launch_ready_target"
        )

    base_dir = Path(config_path).resolve().parent if config_path else Path.cwd()
    defaults = {"assets_dir": "assets", "artifacts_dir": "artifacts"}
    for name, default in defaults.items():
        path = _directory(merged.get(name, default), name)
        merged[name] = path if path.is_absolute() else (base_dir / path).resolve()

    known = {f.name for f in fields(MobileConfig)}
    return MobileConfig(**{k: v for k, v in merged.items() if k in known})


def _read_toml(config_path: str | Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}
    path = Path(config_path)
    if not path.is_file():
        raise ConfigurationError(f"配置文件不存在:{path}")
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"配置文件不是合法的 TOML({path}):{exc}") from exc
    unknown = sorted(set(data) - _TOML_FIELDS)
    if unknown:
        raise ConfigurationError(f"配置文件包含未知配置项:{', '.join(unknown)}")
    return data


def _target_from_toml(data: Any, field_name: str) -> Target:
    if not isinstance(data, dict):
        raise _invalid(field_name, "必须是表格(by/value,可选 index、confidence、region)")
    unknown = sorted(set(data) - {"by", "value", "index", "confidence", "region"})
    if unknown:
        raise _invalid(field_name, f"定位描述包含未知字段:{', '.join(unknown)}")
    region = data.get("region")
    try:
        return Target(
            by=data.get("by"),
            value=data.get("value"),
            index=data.get("index"),
            confidence=data.get("confidence"),
            region=tuple(region) if region is not None else None,
        )
    except InvalidTarget as exc:
        raise ConfigurationError(f"配置项 {field_name} 非法:{exc}") from exc


__all__ = [
    "ENV_APP_ACTIVITY",
    "ENV_APP_PACKAGE",
    "ENV_DEVICE_SERIAL",
    "MobileConfig",
    "load_config",
]
