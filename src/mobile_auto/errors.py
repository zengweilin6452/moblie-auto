"""本包的可诊断错误类别(plan 5.4)。

每类对应一种具体调用失败,没有 catch-all 后成功返回的路径。
所有错误继承 MobileAutoError,便于调用方统一捕获后按类别处理。
"""


class MobileAutoError(Exception):
    """本包所有错误的公共基类。"""


class InvalidTarget(MobileAutoError):
    """定位描述非法:字段缺失、类型错误、取值越界或不支持的定位方式。"""


class ConfigurationError(MobileAutoError):
    """配置非法:参数、环境变量或 TOML 合并后的值无法通过校验。"""


class DeviceUnavailable(MobileAutoError):
    """设备当前不可达:未连接、掉线或序列号不匹配。"""


class DeviceBusy(MobileAutoError):
    """设备被其他进程的租约占用。"""


class ElementNotFound(MobileAutoError):
    """查找目标在限定时间内不存在。"""


class AmbiguousTarget(MobileAutoError):
    """单目标操作匹配到多个可见元素且未指定序号。"""


class StaleHandle(MobileAutoError):
    """查询句柄已过期:页面、模板或会话身份无法再次确认,不自动指向新节点。"""


class UnsupportedOperation(MobileAutoError):
    """该操作对此类目标不支持(如对 IMAGE 目标取文本)。"""


class WaitTimeout(MobileAutoError):
    """等待预算耗尽。"""


class InvalidTemplate(MobileAutoError):
    """模板图片或元数据非法:损坏、低纹理、摘要不符或版本缺失。"""


class MatchOverflow(MobileAutoError):
    """模板匹配候选或结果数量超过上限,需要收缩搜索区域或提高阈值。"""


class OperationOutcomeUnknown(MobileAutoError):
    """操作已进入发送阶段后被取消,设备侧结果未知;不自动重放。"""


class SessionFaulted(MobileAutoError):
    """会话故障:在途操作结果无法确认,会话拒绝后续设备操作。"""


class StreamUnavailable(MobileAutoError):
    """scrcpy 视频流/控制通道不可用;属能力降级信号,上层回退 u2,不是会话故障。"""


__all__ = [
    "AmbiguousTarget",
    "ConfigurationError",
    "DeviceBusy",
    "DeviceUnavailable",
    "ElementNotFound",
    "InvalidTarget",
    "InvalidTemplate",
    "MatchOverflow",
    "MobileAutoError",
    "OperationOutcomeUnknown",
    "SessionFaulted",
    "StaleHandle",
    "StreamUnavailable",
    "UnsupportedOperation",
    "WaitTimeout",
]
