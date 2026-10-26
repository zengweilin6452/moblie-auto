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

`Target.index` 是**树遍历序**(uiautomator 层级顺序),不是视觉位置序;
列表顺序与阅读顺序不一致时以层级顺序为准(实现见 `core/selectors.py`)。

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

**坐标基准(契约外观测,2026-10-26)**:失败证据 `meta.device.coordinate_spaces`
记录最近一次注入前的通道/后端两组尺寸与刷新结果。键集以实际实现为准:

- 必有:`channel`、`backend`、`refreshed`、`refreshable`、`consistent`、
  `checked_at`(UTC 毫秒);
- 可选:`refresh_error`(刷新失败时的异常类名)、`reason`(显式 u2 且无可用
  视频通道时,说明为何未校验)。

`channel` 为通道缓存尺寸,无法判定时为 `null`(如显式 `u2` 且未启动视频通道)。
未发生注入(或确无基准可记)时 `coordinate_spaces` 为 `null`;不属于错误类型,
绑定层无需映射,原样透传即可。注入前基准校验与刷新由本包内部完成(节流 1s,
见 `docs/validation.md`),契约方法签名不变。

## 5. 契约版本记录

| 日期 | 契约内容 | 依据 |
| :--- | :--- | :--- |
| 2026-10-24 | 15 方法 + platform;13 类错误;六种 Locator;语义以 plan 5.2/5.3 为准 | 工单 01–11 交付 |
| 2026-10-25 | `goto` 新增可选 `package`/`activity` 显式覆盖(缺省沿用配置,行为向后兼容);`activity` 必须与 `package` 同时给出 | 同一会话切换被启动 App 的需要 |
| 2026-10-25 | 新增可选配置 `dialog_guard`(契约外行为,默认关闭):预算耗尽时按白名单清理一次系统弹窗,命中才重试一次 | MIUI 权限弹窗随机挡住锚点等待(见 §10) |
| 2026-10-26 | 坐标基准生命周期(契约外):注入前校验缓存尺寸 vs 实时 `wm size` 并刷新(1s 节流);证据新增 `meta.device.coordinate_spaces` | 旋转/折叠后按旧基准点击(工单 04) |
| 2026-10-26 | 契约外新增 `wait_gone`/`click_until_gone`(15 方法签名与语义不变;绑定层按需转发,见 §12) | 工单 05:消失等待原语 |

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

## 10. 系统弹窗守卫(契约外行为,2026-10-25)

MIUI 系统权限弹窗(`com.lbe.security.miui`)会随机挡住锚点等待,导致
本可成功的步骤超时。守卫是**显式配置的可选行为**,默认关闭:

```toml
# 只配"拒绝/Reject"以保持既有授权状态;不要配"允许"。
[[dialog_guard]]
by = "u2_selector"
value = '{"packageName":"com.lbe.security.miui","text":"拒绝"}'
```

语义:
- 只在**等待预算耗尽**时触发:`click`/`input_text`/`get_text`/
  `get_attribute`(经 `_poll_until`)、`wait_for`、`wait_for_load`,
  以及图像点击首次无匹配;`is_visible` 仍是单次查询,不触发。
- 每次操作最多清理一次;只有**实际点到**白名单弹窗才重置一次预算并重试,
  未命中不延长等待。最坏耗时为 2× `wait_timeout`。
- 白名单只接受 `u2_selector` 且必须带非空 `packageName`(配置层校验),
  避免把业务页面的"取消/确定"误当弹窗;匹配不唯一时跳过,不猜。
- 清理与普通点击同一语义:经设备会话队列串行、发送前过取消检查点;
  失败证据 `meta.device.dialog_guard` 记录 `attempts`/`dismissed`/`last_at`,
  失败信息注明"已尝试清理系统弹窗",不冒充静默重试。
- ANR/崩溃对话框**不自动处理**:按原错误失败并留证据(自动点"等待"会
  掩盖真实问题)。
- 更彻底的方案是 setup 阶段预授权已知权限(`appops`/`pm grant`),让弹窗
  不出现;守卫只作为不可预授权弹窗的兜底。

回滚:删除 `dialog_guard` 配置即恢复原行为,无数据迁移。

## 11. 动作流水(契约外观测,2026-10-26)

每次公共调用一行 JSONL,作为 flake 定位、每步耗时归因与 viewer 回放的数据源。
记录点在 `MobileAdapter` 公共方法与 `adapter.device` 手势面(装饰器),
不在设备队列内部:一次 `wait_for` 只一行,含完整轮询耗时。

- **落盘**:pytest 侧跟随 `--mobile-evidence`,与失败证据同源——
  `<artifacts>/xdist-<worker>/actions.jsonl`,非 xdist 为
  `<artifacts>/actions.jsonl`;viewer 侧始终写
  `<artifacts>/viewer/<serial>/actions.jsonl`(按设备分层)。viewer 的 serial
  目录名做清洗:只保留 `[0-9A-Za-z._-]`,其余字符替换为 `_`(含 `:` 的 TCP
  serial 不能直接做目录名,如 `192.168.1.5:5555` → `192.168.1.5_5555`)。
  目录不可写或写入失败时自动禁用并一次性告警,不改变操作结果。单文件上限
  32 MiB,超限追加 `{"op":"journal","outcome":"truncated"}` 后停止写入。
- **字段**:`ts`(UTC 毫秒)/`session`(会话 8 位随机 id)/`op`/
  `duration_ms`/`outcome`(`ok`/`not_found`/`error`/`unknown`;`unknown` 仅用于
  **已进入发送阶段**的取消——发送前取消记 `error` + `error_type="CancelledError"`)/
  `error_type`(异常类名,不含消息)/`channel`(仅注入类操作,取注入后实际使用的
  通道;读不到则省略,不写死)/`target`(只含 `by`/`value`/`index`/`confidence`/
  `region`)/`coordinate_basis`(注入前基准,见 §4)。
- **操作与专属字段**:
  - 契约方法:`wait_for`/`wait_gone`/`click_until_gone` 预算耗尽成功返回
    `False` 时记 `outcome="not_found"`;
  - `adapter.device`: `device.tap`/`device.long_press` 记 `point` + `normalized`,
    `device.swipe` 记 `from`/`to` + `normalized`(坐标面均为设备像素,
    `normalized=false`);
  - viewer 注入:`viewer.tap`(`point`/`normalized`)、`viewer.swipe`
    (`from`/`to`/`normalized`)、`viewer.press_key`(`key`)、`viewer.wake`
    (无专属字段),均写 `coordinate_basis`(tap/swipe)与实际 `channel`。
- **不记 `channel` 的注入路径**:原生 `click`/`press_key` 等契约方法经 u2 后端
  直连(不经 `inject_gesture`),没有可观测的通道切换;这些行不带 `channel`
  字段——这是既定取舍:链路确实没经过视频通道时,不臆造 "u2"。
- **脱敏**:`input_text` 只记 `text_length`,不记原文;`get_text` 不记返回值;
  `goto` 只记 `scheme` 或 `"cold_start"`;读取类不带 `channel`。
- **消费者**:失败证据 `meta.json` 的 `journal_summary`(步数/总耗时/首个失败步);
  viewer 证据面板「动作流水」页签(端点
  `/api/v1/evidence/{id}/journal`,按证据同 worker 目录反查,不走
  `/files/{name}` 白名单)。
- **前提**:动作流水假设设备访问都经 `MobileAdapter`;绑定层直接调用
  `DeviceSession`/`DeviceBackend` 会漏记。绑定层应委托适配器公共方法。
- **日志器命名约定**:按功能命名,`mobile_auto.dialog_guard`(弹窗守卫)与
  `mobile_auto.journal`(动作流水)是仅有的两个;只经模块级 logger 输出
  `warning`/`info`,不自建 handler,观测失败不得影响操作结果。

回滚:删除装饰器与 `evidence/journal.py` 即可,无数据迁移。

## 12. 契约外便捷方法(建议绑定层转发,2026-10-26)

以下方法**不在 15 方法契约内**(签名与语义均未改动),但属适配器公共面。
内网要在线用例里用上,绑定层必须显式加转发(每个方法一行委托);不加转发
则内网 Port 上根本看不到这些方法,契约核对也不受影响。

| 方法 | 签名 | 语义 |
| :--- | :--- | :--- |
| `adapter.device.tap` | `(x, y, *, source="auto")` | 坐标点击;返回实际注入通道 |
| `adapter.device.swipe` | `(x0, y0, x1, y1, duration_s=0.3, *, source="auto")` | 坐标滑动;返回通道 |
| `adapter.device.long_press` | `(x, y, hold_s=0.8, *, source="auto")` | 坐标长按;返回通道 |
| `adapter.wait_gone` | `(target, timeout=None) -> bool` | 轮询到目标**不可见**即 True;预算耗尽 False |
| `adapter.click_until_gone` | `(target, timeout=None, interval=0.5) -> bool` | 反复点击到目标消失;预算耗尽 False |

```python
# 绑定层转发示例(内网仓库;不改 15 方法契约)
async def wait_gone(self, target, timeout=None):
    return await self._adapter.wait_gone(target, timeout=timeout)

async def click_until_gone(self, target, timeout=None, interval=0.5):
    return await self._adapter.click_until_gone(
        target, timeout=timeout, interval=interval
    )
```

- `wait_gone` 与 `wait_for` 对称:默认 `wait_timeout`、按 `poll_interval`
  轮询,`timeout=0` 只查询一次,负数/非有限值报 `ValueError`,设备错误原样
  抛出;预算耗尽时动作流水记 `outcome="not_found"`。不触发弹窗守卫。
- `click_until_gone` **仅用于点击后自身消失的控件**(弹窗关闭、提示确认)。
  点击会跳转页面时目标不再出现,会连点到预算耗尽。原生目标每轮一个队列
  任务(查找→唯一定位→取消检查点→点击),`AmbiguousTarget` 直接抛不猜;
  IMAGE 目标每轮一次完整点击事务,不做"先查询再事务"的双重采集。轮间
  `sleep(min(interval, 剩余预算))`,`interval` 必须为有限正数;预算耗尽
  同样在动作流水记 `outcome="not_found"`。
- **决策**:不提供 `raise_on_timeout` 开关;需要断言消失就写
  `assert await adapter.wait_gone(...)`——`False` 已是明确返回值,再加开关
  只会分裂两条失败语义。
- `tests/contract/test_contract.py` 的"契约面封闭"断言显式白名单了这两个
  方法(`EXTRA_PUBLIC_METHODS`);新增同类方法须同步更新白名单与本节。

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
