# 内网接入指南(工单 13)

本包独立于内网框架开发;接入 = 部署在内网仓库的**绑定层**把真实 Port 接到
本包执行核心(见 `.scratch/mobile-foundation/plan.md` 第 13 节的门槛与分工)。
本文件给出绑定骨架、契约测试模板、契约版本记录、错误映射、引擎对照与
OpenCV 共存验证步骤。GLOSSARY"参考契约/绑定层"为术语依据。

## 1. 参考契约(本地形状)

`mobile_auto.MobileAdapter` 暴露 15 个异步方法 + `platform` 属性:

| 方法 | 签名摘要 | 本地语义(详见 plan 5.3) |
| :--- | :--- | :--- |
| goto | `(target: str = "", package=None, activity=None)` | 空串冷启动 App(package/activity 显式覆盖配置);非空按 Deep Link 白名单 |
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

**契约外平台扩展面(2026-10-25,绑定层不映射)**:`adapter.device.*`
提供坐标手势原语 `tap(x, y)` / `swipe(x0, y0, x1, y1, duration_s)` /
`long_press(x, y, hold_s)`,可选 `source="auto"|"scrcpy"|"u2"` 钉死注入
通道(auto=scrcpy 控制流优先、首写失败整体回退 u2);返回实际使用通道。
属移动端专属能力,不在 15 方法契约内、契约测试不涉及;内网绑定层
按需自行决定是否暴露,不映射不影响契约核对。

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
| 2026-10-25 | `goto` 新增可选 `package`/`activity` 显式覆盖(缺省沿用配置,行为向后兼容);`activity` 必须与 `package` 同时给出 | 同一会话切换被启动 App 的需要 |

内网接入完成后追加:真实 Port 枚举值 ↔ 本地 Locator 映射表、行为差异
(如 marker 默认值)、绑定层版本号。

## 6. 内网交接门槛(未提供即阻塞)

- **ListItemSchema**:extract_list 未实现且不伪造;拿到完整字段、嵌套规则
  与行为样例(普通行/缺字段/空列表/max_items)后才实现绑定。
- **真实 Port 默认值与异常语义**:由内网框架维护者提供;bind 期间核对。
- **内网 Python 版本**:本包以 3.12 首测;内网版本 wheel 可用性由接入负责人验证。

## 7. 引擎对照(与内网 ImageMatcher)

- 样例集:源码树 `tests/fixtures/images/`(12 组真实正负样本 + README +
  samples.json 清单,随源码交付),引擎纯函数为 `mobile_auto.image.match_template`。
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

## 9. 失败证据钩子接入(P1)

`mobile-auto` 注册了 pytest 插件(入口点 `pytest11: mobile-auto-evidence`),
不开启零行为,内网按需启用:

```text
pytest --mobile-evidence ...
```

- 用例 call 阶段失败时,插件在本进程 funcargs 中寻找 `DeviceSession`
  (优先名为 `mobile_session` 的 fixture,其次任意 `DeviceSession` 实例),
  调用失败取证并把证据路径写入 `report.sections("mobile-auto-evidence")`,
  兼容 pytest-html / junit 展示;取证失败不影响原始失败上报。
- 证据内容:失败瞬间画面(scrcpy 视频帧优先,u2 截图回退,`frame_source`
  如实标注)+ UI 树 + 设备基线;写入产物目录并登记 `run-manifest.jsonl`
  (清单是读取证据的唯一入口,沿用 P0 约定)。
- fixture 形态参考 `examples/evidence_flow.py` 与 `tests/device/conftest.py`:
  session 级设备会话 + function 级适配器,资源所有权约定同 §2。
- 挂接内网真实报告:报告插件读取 `report.sections` 或
  `report.user_properties` 中的 `mobile_auto_evidence` 条目即可;
  不要求修改内网 conftest 之外的框架代码。

## 并行执行与 CI 投屏接入(P2,2026-10-25)

**设备池并行(pytest-xdist)**:池协调即设备租约目录,无中心服务。

```bash
# 配置:local.toml 声明候选(device_pool = ["serialA", "serialB"]),
# 或运行期旗标覆盖(--mobile-devices 优先级高于 TOML)
pytest tests/ -n 2 --mobile-devices=serialA,serialB
```

- 每 worker 独立进程非阻塞抢租约,各绑一台;全忙 60s 后明确报 DeviceBusy。
- 产物按 worker 分目录(artifacts/xdist-gwN/),运行清单与失败证据互不覆盖。
- 依赖:pytest-xdist 由接入方按内网策略安装(未入本包 dev extras);
  带真机的执行机须 Windows(设备租约当前仅支持 Windows 文件区域锁)。
- 单设备池等价现状;fixture 可被 conftest 同名覆盖(绑定层自由替换)。

**CI 可选投屏(--debug-viewer)**:默认关闭零开销,按需开启。

```bash
pytest tests/ --debug-viewer          # controller 进程后台拉起 viewer 并打印 URL
```

- 仅 controller/主进程启动一份(xdist worker 不起);端口默认配置 port,
  被占自动递增;缺 viewer extra(fastapi/uvicorn)报 UsageError 不静默。
- 预览读取不占设备租约,投屏不影响用例执行与结果。

**占用/运行态数据的一致性前提**:viewer(serve 或 --debug-viewer)要与
pytest 运行**共用同一 --mobile-config**(artifacts_dir 一致),否则
/api/v1/leases、/api/v1/runs、/api/v1/evidence 看不到对方数据。

## 视频通道后端选择(P2 上游迁移,2026-10-25)

`stream_backend = upstream | wetest`(TOML 同名键,默认 **upstream**)。

- **upstream(默认)**:上游官方 scrcpy server v5.0.1(Apache-2.0,随包资产),
  自研薄客户端。**Android 16 QPR2+/17 必须用此后端**(wetest 内嵌 server 在
  新系统的 binder 回调上崩溃,issue #6362)。
- **wetest(回退)**:内嵌协议 fork server(CAPTURE 全分辨率取证截图为其独有
  能力);Android 17+ 不可用。

**取证截图分辨率语义**:upstream 后端的 `capture_png` 返回当前解码帧,分辨率=
流分辨率(`max_size` 默认 1080);需要设备原生分辨率取证时配置
`screenshot_source = "u2"`(u2 通道与后端选择正交)。详见 docs/prd-auto.md §8.5。

资产就位:`python scripts/fetch_scrcpy_server.py`(幂等校验双后端资产;
上游项在 GitHub 不可达时可手工放置后重跑校验)。协议与验收细节见
`.scratch/scrcpy-upstream/spec.md`(本地保留)。
