"""配置定义、合并与校验(plan 9.1)。

优先级:显式构造参数 > 环境变量 > TOML > 默认值。
只读取三个沿用内网名称的环境变量(MOBILE_DEVICE_SERIAL /
MOBILE_APP_PACKAGE / MOBILE_APP_ACTIVITY),不新增环境变量。

TOML 为可选的单一配置文件,路径由调用方(如 CLI --config)指定;
键与字段同名。assets_dir / artifacts_dir 相对路径基于配置文件所在目录解析,
未指定配置文件时基于工作目录;load_config 返回的两者均为绝对路径。
launch_ready_target 用 TOML 表格表示
(by/value,可选 index、confidence、region),by 取 Locator 的小写字符串值。
dialog_guard 用 TOML 表格数组表示(每项同 launch_ready_target 形状),
但只允许 u2_selector 且必须带非空 packageName:白名单必须限定到系统弹窗
所属包,避免把业务页面的同名按钮(如"取消")误当成弹窗。
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
        "device_pool",
        "app_package",
        "app_activity",
        "assets_dir",
        "artifacts_dir",
        "launch_ready_target",
        "allowed_deeplink_schemes",
        "dialog_guard",
        "wait_timeout",
        "poll_interval",
        "port",
        "screenshot_source",
        "injection_source",
        "stream_backend",
    }
)

_SCREENSHOT_SOURCES = frozenset({"auto", "u2", "scrcpy"})
_INJECTION_SOURCES = _SCREENSHOT_SOURCES
_STREAM_BACKENDS = frozenset({"wetest", "upstream"})
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
    # 设备池(P2 工单 05):并行执行时按序尝试获取租约的候选设备;
    # 空元组表示未配置(单设备语义由 device_serial 承担)
    device_pool: tuple[str, ...] = ()
    app_package: str | None = None
    app_activity: str | None = None
    assets_dir: Path = Path("assets")
    artifacts_dir: Path = Path("artifacts")
    launch_ready_target: Target | None = None
    allowed_deeplink_schemes: tuple[str, ...] = ()
    # 系统弹窗白名单:等待预算耗尽时按序查找并点击命中项(仅 u2_selector,
    # 且必须带 packageName 限定到系统弹窗所属包);空元组 = 完全关闭
    dialog_guard: tuple[Target, ...] = ()
    wait_timeout: float = 10.0
    poll_interval: float = 0.5
    port: int = 8765
    # 驱动截图通道:auto=scrcpy 优先、故障回退 u2;u2=强制 u2;scrcpy=强制且不回退
    screenshot_source: str = "auto"
    # 注入通道(scroll/图像点击发送/press_key back/home 与 adapter.device):
    # 同一语义;u2 可钉死时序敏感用例(手势插值 u2 在设备侧、scrcpy 在主机侧)
    injection_source: str = "auto"
    # 视频流通道后端(P2 上游迁移):upstream=上游官方 server v5.0.1
    # (Android 16 QPR2+/17 兼容,Apache-2.0,双真机验收通过后为默认);
    # wetest=内嵌协议 fork(Android<17,保留为回退选项)
    stream_backend: str = "upstream"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "device_serial", _optional_str(self.device_serial, "device_serial")
        )
        object.__setattr__(
            self, "device_pool", _serials(self.device_pool, "device_pool")
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
            self, "dialog_guard", _dialog_targets(self.dialog_guard, "dialog_guard")
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
        if self.stream_backend not in _STREAM_BACKENDS:
            raise _invalid(
                "stream_backend",
                f"必须是 {' / '.join(sorted(_STREAM_BACKENDS))} 之一",
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


def _serials(value: Any, field_name: str) -> tuple[str, ...]:
    """非空字符串列表 → 去重保序元组(设备池候选)。"""
    if value in (None, (), ""):
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise _invalid(field_name, "必须是字符串列表")
    serials: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _invalid(field_name, "每一项都必须是非空字符串")
        item = item.strip()
        if item not in serials:
            serials.append(item)
    return tuple(serials)


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


def _dialog_targets(value: Any, field_name: str) -> tuple[Target, ...]:
    """弹窗白名单:只接受以 packageName 限定的 u2 选择器。

    业务页面同样可能出现"取消/确定"按钮,仅凭文本无法区分;把
    packageName 设为必填,是从配置层面排除误点业务控件的唯一可靠约束。
    """
    if value in (None, (), []):
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise _invalid(field_name, "必须是 Target 列表")
    from .core.selectors import parse_u2_selector

    targets: list[Target] = []
    for item in value:
        if not isinstance(item, Target):
            raise _invalid(field_name, "每一项都必须是 Target")
        if item.by.value != "u2_selector":
            raise _invalid(
                field_name, "每项必须用 u2_selector(需 packageName 限定包名)"
            )
        try:
            parsed = parse_u2_selector(item.value)
        except InvalidTarget as exc:
            raise _invalid(field_name, str(exc)) from exc
        if not parsed.get("packageName"):
            raise _invalid(
                field_name,
                "u2_selector 必须包含非空 packageName,避免误点业务页面的同名按钮",
            )
        targets.append(item)
    return tuple(targets)


def load_config(
    config_path: str | Path | None = None,
    *,
    device_serial: Any = _UNSET,
    device_pool: Any = _UNSET,
    app_package: Any = _UNSET,
    app_activity: Any = _UNSET,
    assets_dir: Any = _UNSET,
    artifacts_dir: Any = _UNSET,
    launch_ready_target: Any = _UNSET,
    allowed_deeplink_schemes: Any = _UNSET,
    dialog_guard: Any = _UNSET,
    wait_timeout: Any = _UNSET,
    poll_interval: Any = _UNSET,
    port: Any = _UNSET,
    screenshot_source: Any = _UNSET,
    injection_source: Any = _UNSET,
    stream_backend: Any = _UNSET,
) -> MobileConfig:
    """按 显式参数 > 环境变量 > TOML > 默认值 的优先级合并配置并校验。"""
    toml_data = _read_toml(config_path)
    explicit = {
        "device_serial": device_serial,
        "device_pool": device_pool,
        "app_package": app_package,
        "app_activity": app_activity,
        "assets_dir": assets_dir,
        "artifacts_dir": artifacts_dir,
        "launch_ready_target": launch_ready_target,
        "allowed_deeplink_schemes": allowed_deeplink_schemes,
        "dialog_guard": dialog_guard,
        "wait_timeout": wait_timeout,
        "poll_interval": poll_interval,
        "port": port,
        "screenshot_source": screenshot_source,
        "injection_source": injection_source,
        "stream_backend": stream_backend,
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
    if isinstance(merged.get("dialog_guard"), list):
        merged["dialog_guard"] = tuple(
            _target_from_toml(item, "dialog_guard")
            for item in merged["dialog_guard"]
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
