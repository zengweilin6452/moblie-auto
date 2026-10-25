"""mobile-auto 命令行入口(plan 9.2)。

doctor:仅执行无副作用检查——报告依赖版本、ADB 可见性、已授权设备在线状态;
不触发设备端组件初始化、不安装、不写入设备。缺依赖或 ADB 不可用时非零退出。
serve:随网页阶段交付,此处尚不注册。

uiautomator2 / adbutils 延迟到函数内导入,保证包导入本身无设备副作用。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import MobileConfig, load_config
from .core.backend import (
    AdbProbeError,
    DeviceEntry,
    list_adb_devices,
)
from .core.backend import (
    find_adb_binary as find_adb,
)
from .errors import ConfigurationError

EXIT_OK = 0
EXIT_ENVIRONMENT = 1
EXIT_CONFIG = 2

# (发行包名, 展示名);核心依赖缺失导致 doctor 失败,可选 extra 缺失只提示。
_CORE_DEPENDENCIES = (
    ("uiautomator2", "uiautomator2"),
    ("pillow", "Pillow"),
)
_OPTIONAL_DEPENDENCIES = (
    ("opencv-python-headless", "OpenCV(headless)"),
    ("numpy", "NumPy"),
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
)


@dataclass(frozen=True)
class DependencyStatus:
    display_name: str
    required: bool
    version: str | None  # None 表示未安装


@dataclass
class DoctorReport:
    python_version: str
    dependencies: list[DependencyStatus]
    adb_path: str | None
    adb_error: str | None
    devices: list[DeviceEntry]
    config_file: str | None
    device_serial: str | None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_OK if not self.problems else EXIT_ENVIRONMENT

    def render(self) -> str:
        lines = [
            "mobile-auto doctor(无副作用检查:不初始化设备端组件、不安装、不写入设备)",
            "说明:探测设备状态可能启动本机 adb 服务,属主机侧行为。",
            "",
            f"Python: {self.python_version}",
            f"配置文件: {self.config_file or '未指定(使用默认值)'}",
            f"设备序列号(MOBILE_DEVICE_SERIAL/配置): {self.device_serial or '未设置'}",
            "",
            "依赖:",
        ]
        for dep in self.dependencies:
            state = dep.version if dep.version is not None else "未安装"
            label = "必需" if dep.required else "可选"
            lines.append(f"  [{label}] {dep.display_name}: {state}")
        lines.append("")
        if self.adb_path is None:
            lines.append("ADB: 未找到")
        else:
            lines.append(f"ADB: {self.adb_path}")
            if self.adb_error is None:
                lines.append("  adb devices: 成功")
            else:
                lines.append(f"  adb devices: 失败({self.adb_error})")
        lines.append("")
        lines.append(f"设备(共 {len(self.devices)} 台):")
        for title, group in self.device_groups().items():
            detail = ", ".join(d.serial for d in group) if group else "-"
            lines.append(f"  {title}: {len(group)} 台 — {detail}")
        lines.append("")
        for title, items in (("警告", self.warnings), ("问题", self.problems)):
            if items:
                lines.append(f"{title}:")
                lines.extend(f"  - {item}" for item in items)
                lines.append("")
        verdict = "环境存在问题上文已列出" if self.problems else "环境可用"
        lines.append(f"结果:{verdict}(退出码 {self.exit_code})")
        return "\n".join(lines)

    def device_groups(self) -> dict[str, list[DeviceEntry]]:
        return {
            "已授权在线": [d for d in self.devices if d.authorized],
            "未授权": [d for d in self.devices if d.state == "unauthorized"],
            "离线": [d for d in self.devices if d.state == "offline"],
            "其他状态": [
                d
                for d in self.devices
                if d.state not in {"device", "unauthorized", "offline"}
            ],
        }


def _installed_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_doctor(
    config: MobileConfig,
    config_path: str | Path | None = None,
    *,
    version_of=_installed_version,
    adb_finder=find_adb,
    device_lister=list_adb_devices,
) -> DoctorReport:
    """执行全部无副作用检查;检查手段可注入,便于在测试中替换。"""
    problems: list[str] = []
    warnings: list[str] = []

    dependencies = []
    for distribution, display in _CORE_DEPENDENCIES:
        version = version_of(distribution)
        dependencies.append(DependencyStatus(display, required=True, version=version))
        if version is None:
            problems.append(f"缺少必需依赖 {display}(请在项目环境中重新执行 pip install -e .)")
    for distribution, display in _OPTIONAL_DEPENDENCIES:
        version = version_of(distribution)
        dependencies.append(DependencyStatus(display, required=False, version=version))
        if version is None:
            warnings.append(f"可选依赖 {display} 未安装(对应 extra 未安装时属预期)")

    adb_path = adb_finder()
    devices: list[DeviceEntry] = []
    adb_error: str | None = None
    if adb_path is None:
        problems.append("未找到 adb(应随 uiautomator2 的 adbutils 分发,或在 PATH 中)")
    else:
        try:
            devices = device_lister(adb_path)
        except AdbProbeError as exc:
            adb_error = str(exc)
            problems.append(f"adb 探测失败:{exc}")

    authorized = {d.serial for d in devices if d.authorized}
    if not problems and not authorized:
        if devices:
            warnings.append("没有已授权在线设备;未授权设备需在手机上确认 USB 调试授权")
        else:
            warnings.append("未发现任何设备")

    if adb_error is None and config.device_serial and config.device_serial not in authorized:
        warnings.append(
            f"配置的设备序列号 {config.device_serial} 不在已授权在线设备中"
        )

    return DoctorReport(
        python_version=platform.python_version(),
        dependencies=dependencies,
        adb_path=adb_path,
        adb_error=adb_error,
        devices=devices,
        config_file=str(config_path) if config_path else None,
        device_serial=config.device_serial,
        problems=problems,
        warnings=warnings,
    )


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        print(f"配置错误:{exc}", file=sys.stderr)
        return EXIT_CONFIG
    report = run_doctor(config, args.config)
    print(report.render())
    return report.exit_code


def _cmd_serve(args: argparse.Namespace) -> int:
    """启动本机网页服务:仅监听 127.0.0.1;多设备经 ?device= 寻址、懒连接。"""
    import uvicorn

    from .viewer.app import _DeviceRegistry, create_app

    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        print(f"配置错误:{exc}", file=sys.stderr)
        return EXIT_CONFIG
    registry = _DeviceRegistry(config)  # 会话在首次被寻址时才连接设备
    app = create_app(registry=registry)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    finally:
        # Ctrl+C/停机信号经 uvicorn 优雅退出后,显式关停设备注册表:
        # 停 scrcpy 通道与会话(P2 工单 01;atexit 为进程退出兜底)
        registry.shutdown()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mobile-auto", description="Android 移动能力包命令行"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="环境自检:依赖版本、ADB 可见性、设备在线状态(无副作用)",
    )
    doctor_parser.add_argument(
        "--config", type=Path, default=None, help="TOML 配置文件路径"
    )
    serve_parser = subparsers.add_parser(
        "serve",
        help="启动本机网页调试服务(仅监听 127.0.0.1,默认端口 8765)",
    )
    serve_parser.add_argument(
        "--config", type=Path, default=None, help="TOML 配置文件路径"
    )
    serve_parser.add_argument("--port", type=int, default=8765, help="服务端口")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "serve":
        return _cmd_serve(args)
    raise AssertionError(f"未处理的命令:{args.command}")


if __name__ == "__main__":
    sys.exit(main())
