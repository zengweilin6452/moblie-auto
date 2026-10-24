"""mobile_auto:Android 移动能力包。

导入本包不产生任何设备副作用:不连接设备、不启动 adb、不安装设备端组件。
设备相关能力由明确的会话与命令触发;公共导出集合随各 ticket 交付逐步扩充。
"""

from importlib.metadata import PackageNotFoundError, version

from .config import MobileConfig, load_config
from .contracts import ElementHandle, Locator, Target
from .core.adapter import MobileAdapter
from .core.session import DeviceSession
from .errors import *  # noqa: F401,F403
from .errors import __all__ as _errors_all

try:
    __version__ = version("mobile-auto")
except PackageNotFoundError:  # 源码目录内直接导入时未安装
    __version__ = "0.0.0+unknown"

__all__ = [
    "DeviceSession",
    "ElementHandle",
    "Locator",
    "MobileAdapter",
    "MobileConfig",
    "Target",
    "__version__",
    "load_config",
    *_errors_all,
]
