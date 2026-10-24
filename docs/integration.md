# 内网接入指南(工单 13)

本包独立于内网框架开发;接入 = 部署在内网仓库的**绑定层**把真实 Port 接到
本包执行核心(见 `.scratch/mobile-foundation/plan.md` 第 13 节的门槛与分工)。
本文件给出绑定骨架、契约测试模板、契约版本记录、错误映射、引擎对照与
OpenCV 共存验证步骤。GLOSSARY"参考契约/绑定层"为术语依据。

## 1. 参考契约(本地形状)

`mobile_auto.MobileAdapter` 暴露 15 个异步方法 + `platform` 属性:

| 方法 | 签名摘要 | 本地语义(详见 plan 5.3) |
| :--- | :--- | :--- |
| goto | `(target: str = "")` | 空串冷启动配置 App;非空按 Deep Link 白名单 |
| wait_for_load | `()` | 仅等待配置 launch_ready_target,超时 WaitTimeout |
| back / press_key | `()` / `(name)` | 有限键表 back/home/enter/search/delete/recent |
| click | `(target: Target)` | 单目标,预算内等待;IMAGE 走单事务 |
| input_text | `(target, text)` | 清空并替换;不可编辑拒绝;仅原生 |
| get_text | `(target)` | 原生文本;缺失为空串;IMAGE 不支持 |
| get_attribute | `(target, name)` | 有限属性表;布尔 "true"/"false";bounds 四整数 |
| wait_for | `(target, timeout=None)` | 返回 bool;设备错误原样抛出 |
| is_visible | `(target)` | 单次查询 |
| find_elements | `(target)` | 全部可见匹配或按 index 一个;ElementHandle 列表 |
| extract_list | — | **未实现**(待内网 ListItemSchema,见 §6) |
| screenshot | `()` | 新采集 PNG bytes |
| scroll | `(direction, amount)` | down=内容上移;amount 夹限 10% 安全区 |
| close | `()` | 幂等;不停止 App |
| platform | `"android"` | 常量 |

本地类型:`Target(by: Locator, value, index=None, confidence=None, region=None)`、
`Locator`(resource_id/text/accessibility_id/u2_selector/xpath/image)、
`ElementHandle`(click/get_text/input_text/get_attribute + 重校验)、
`MobileConfig`、13 类错误(`mobile_auto.errors`)。

## 2. 绑定骨架(内网仓库部署)

```python
# binding/mobile_binding.py(内网仓库内;不修改内网 Port)
from mobile_auto import (
    DeviceSession, MobileAdapter, MobileConfig, Locator, Target, load_config,
)
from mobile_auto import errors as local_errors


class Binding:
    """真实 Port 实现委托本地适配器;枚举归一化 + 异常映射在此层。"""

    def __init__(self, config_path=None, **explicit):
        self._config = load_config(config_path, **explicit)
        self._session = None
        self._adapter = None

    # 生命周期按内网 Port 约定映射(start/close → 会话建立/关闭)。

    @staticmethod
    def _to_local_target(remote_target):
        # 内网 Target 枚举 → 本地 Locator;不按类名猜类型。
        mapping = {
            # <内网枚举值>: Locator.RESOURCE_ID,  # 接入时按真实枚举填写
        }
        return Target(by=mapping[remote_target.by], value=remote_target.value)

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        # 本地错误 → 内网异常;保留原始 cause(链式)。
        # <内网异常表> 接入时按真实异常类填写,不得吞并类别。
        ...
```

要点:不依赖两个同名类的 isinstance 巧合;PNG bytes/str/bool/None 直接透传;
句柄包装为真实 ElementHandle;输入文本不写日志。

## 3. 契约测试模板(15 方法核对入口)

`tests/contract/` 的契约测试在本地对参考契约运行;内网接入时把"本地断言"
换成"绑定层断言",同一份清单核对 15 个方法不缺不漏:

```
pytest tests/contract -q          # 本地参考契约核对
# 内网侧:pytest <绑定测试> -q --mobile-config <真实环境配置>
```

每方法的核对项与本地语义一致(方法表第三列);内网真实 Port 行为与本地
语义不一致时,在绑定层显式转换并记录到契约版本记录(§5),不静默替换。

## 4. 错误映射

| 本地错误 | 触发 | 建议内网映射 |
| :--- | :--- | :--- |
| InvalidTarget | 定位描述非法/未知属性/未知按键 | 参数错误类 |
| ConfigurationError | 配置缺失/非法/白名单拒绝 | 配置错误类 |
| DeviceUnavailable | 无设备/未授权/连接失败/采集超时 | 设备不可用类 |
| DeviceBusy | 租约被其他进程持有 | 设备占用类 |
| ElementNotFound | 预算内未出现 | 元素不存在类 |
| AmbiguousTarget | 多匹配无序号 | 定位歧义类(内网如无此类,映射前先在绑定层要求 index) |
| StaleHandle | 句柄/模板身份失守、摘要冲突 | 句柄过期类 |
| UnsupportedOperation | IMAGE 文本类操作/未交付方式 | 不支持类 |
| WaitTimeout | wait_for_load 预算耗尽 | 等待超时类 |
| MatchOverflow | 候选/结果超限 | 定位失败类(提示收缩区域) |
| InvalidTemplate | 模板损坏/低纹理/越界 | 模板错误类 |
| OperationOutcomeUnknown | 已发送后取消 | 结果未知类(不得重试) |
| SessionFaulted | 会话故障/关闭后 | 会话故障类 |

## 5. 契约版本记录

| 日期 | 契约内容 | 依据 |
| :--- | :--- | :--- |
| 2026-10-24 | 15 方法 + platform;13 类错误;六种 Locator;语义以 plan 5.2/5.3 为准 | 工单 01–11 交付 |

内网接入完成后追加:真实 Port 枚举值 ↔ 本地 Locator 映射表、行为差异
(如 marker 默认值)、绑定层版本号。

## 6. 内网交接门槛(未提供即阻塞)

- **ListItemSchema**:extract_list 未实现且不伪造;拿到完整字段、嵌套规则
  与行为样例(普通行/缺字段/空列表/max_items)后才实现绑定。
- **真实 Port 默认值与异常语义**:由内网框架维护者提供;bind 期间核对。
- **内网 Python 版本**:本包以 3.12 首测;内网版本 wheel 可用性由接入负责人验证。

## 7. 引擎对照(与内网 ImageMatcher)

- 样例集:随包分发 `mobile_auto/samples/`(12 组真实正负样本 + README +
  samples.json 清单),引擎纯函数为 `mobile_auto.image.match_template`。
- 对照步骤:内网侧用同一组样例分别跑本地引擎与 ImageMatcher,比较 ROI、
  分数、NMS 去重与排序;差异记录到工单 13 的验证记录,复用/替换据证据决定。
- 校准初值见 `mobile_auto/image.py` 模块头与 samples/README.md;调整须记录。

## 8. OpenCV 共存验证

内网已装 opencv-python(非 headless)时:

1. 不以本包依赖覆盖既有发行包:内网安装只装核心 extra(不含 image extra),
   再单独安装 `opencv-python-headless` 与 NumPy 前先跑
   `python -c "import cv2; print(cv2.__version__)"` 记录既有版本。
2. 两发行包同装会互相覆盖 cv2;验证顺序:先装本包核心 → 记录既有 cv2 →
   装图像 extra → 再跑既有内网用例的 import 与冒烟 → 两者 import 与功能
   均正常才可声明共存可用。
3. 未通过共存验证前,不声明内网可安装图像能力(plan 9.3)。
