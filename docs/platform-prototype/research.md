# APP 自动化测试平台：调研与原型说明

核查日期：2026-10-25（Asia/Shanghai）。交付为离线交互原型，不是已接入后端的平台。所有设备、应用、任务、曲线和测试结果均为人工构造的演示数据。未连接 ADB、启动测试或安装依赖。

## 设计结论

保留 `mobile_auto` 与 `mobile_perf` 两个组件，新增平台外壳及宿主适配层。共同管理应用上下文、设备资源、任务和报告；UI 自动化和性能测试分别拥有自己的工作区。第一期只表达已核实的 Android 能力，iOS 不作为已支持功能。

不直接 Fork 一个完整平台作为本期底座：Sonic 服务端已归档；STF 侧重设备管理且有额外服务栈；SoloX 的展示值得参考，但替换现有性能组件没有必要。这里是基于现有代码匹配度的设计判断，并非对这些项目的全面评测。

## 开源方案核查

通过公开检索发现候选，再读取官方仓库 README、目录和 GitHub 元数据。未安装或运行这些平台，功能描述按官方声明；pushed_at 是仓库最后推送时间，不等于版本发布日期、有效维护质量或本地验证结果。

| 方案 / 官方来源 | 核查结果 | 本原型参考点 | 本期边界 |
| --- | --- | --- | --- |
| [Sonic](https://github.com/SonicCloudOrg/sonic-server)、[Web 源码](https://github.com/SonicCloudOrg/sonic-client-web/tree/main/src/views) | 服务端 archived=true；AGPL-3.0；最后推送 2025-03-24。Web 源码包含 Devices、TestSuites、Jobs、Results、ResultDetail、Elements 等页面 | 设备、套件、任务、结果的导航分工；设备调试工作区 | 仅借鉴信息架构，没有复制源码；不作为默认底座 |
| [DeviceFarmer STF](https://github.com/DeviceFarmer/stf)、[LICENSE](https://github.com/DeviceFarmer/stf/blob/master/LICENSE) | archived=false；最后推送 2026-10-25。API 许可证识别为 NOASSERTION，但 LICENSE 正文为 Apache-2.0。官方列出远程控制、设备状态、占用及设备分组 | 设备可用/占用/离线/未授权的显式区分；只读观察与主动控制分离 | 不新增 STF/RethinkDB/ZeroMQ；本地项目仍是 Windows 单机租约范围 |
| [SoloX](https://github.com/smart-test-ti/SoloX) | archived=false；MIT；最后推送 2024-09-09。README 提供性能曲线、报告、比较、Python/API 入口，并记录 iOS 版本限制 | 指标切换、趋势曲线、运行对比 | 不把上游 Android/iOS 支持当作本地项目支持；不直接引入 SoloX |
| [Maestro](https://github.com/mobile-dev-inc/Maestro) | archived=false；Apache-2.0；最后推送 2026-10-24。官方描述 YAML flow 与 Studio 的调试体验 | 清晰的步骤、断言和运行结果关系 | 保留现有 pytest/Python 用例，不增加 YAML 引擎；Cloud 不等同于开源框架 |
| [SoloPi](https://github.com/alipay/SoloPi) | archived=false；Apache-2.0；最后推送 2026-10-23。官方当前仓库包含设备端与 Harness，性能指标依赖设备实际支持 | 采集会话、运行证据、端侧采集与宿主任务分工 | 以本地二开快照为接入依据，不把最新上游所有能力直接算入本地组件 |
| [Allure Report 附件](https://allurereport.org/docs/attachments/)、[历史与重试](https://allurereport.org/docs/history-and-retries/) | 官方文档描述步骤附件、历史和重试的区别。本轮未核查其具体发行版/仓库许可证 | 失败结果先展示现场材料；重跑形成新的运行记录，保留原始失败 | 仅借鉴交互；未声称现有项目安装 Allure，不将商业 TestOps 当开源交付 |

以上仓库状态均为本轮 GitHub API 读取。后续采用第三方代码时应重新核查目标版本、许可证和依赖，本轮原型为原创实现。

## 本地项目核查

| 项目 | 本轮状态 | 已核查代码 | 对界面的影响 |
| --- | --- | --- | --- |
| `D:\workspace\claudecode-test\moblie-auto` | 分支 `feat/scrcpy-upstream`，HEAD `d4beb80`；原有 3 个未跟踪文件：`src/mobile_auto/stream/upstream.py`、`src/mobile_auto/stream/upstream_proto.py`、`tests/unit/test_upstream_proto.py` | `viewer/app.py`、`viewer/static/index.html`、`viewer/server.py`、`pool.py`、`contracts.py`、`viewer/candidates.py`、`GLOSSARY.md`、`.scratch/p2-platform/spec.md`、`.scratch/scrcpy-upstream/spec.md` | 独立设备中心；实时预览与冻结快照分离；原生/图像定位；设备占用只读降级；失败证据入口 |
| `D:\workspace\claudecode-test\moblie-xn` | 分支 `mobile-perf-dev`，HEAD `80aaffb`；核查时工作区干净 | `mobile_perf/kit.py`、`session.py`、`gate.py`、`metrics.py`、`judge.py`、`report.py`、`cli.py`、`GLOSSARY.md`、ADR-0001/0002/0003 | 采集设置、设备就绪状态、同设备档案的基线对比、median 判定、样本数、三态门禁与缺失原因 |

这些是代码静态核查，不是本轮真机或组件测试通过声明。README 与规格中的历史验收记录没有当作本轮验证。没有覆盖原有改动；原型只新增到 `docs/platform-prototype/`。

### UI 模块可复用接口

`viewer/app.py` 已提供 `/api/v1/devices`、`/devices/{serial}/info`、`/leases`、`/runs`、`/evidence`、`/snapshots`、`/pick`、`/locators/validate`、`/templates`、`/control`、`/input/*` 及 WebSocket `/preview`。`/runs` 是 worker 状态读取，不是平台任务提交 API。

`viewer/server.py` 仅绑定 `127.0.0.1`，`app.py` 检查 Host/Origin；不能把它直接放进远程用户浏览器的 localhost iframe 就宣称完成接入。平台需要经过鉴权的宿主代理/网关、明确的设备权限及 WS 转发设计；第一阶段可在同机验证适配，不能为原型放开既有访问边界。

### 性能模块可复用能力

`PerfKit` 提供 `ensure_ready`、`run_collect`、`save_baseline`、`judge`、`trend`；CLI 提供 doctor、collect、report、trend。`report.py` 有 `schemaVersion`、`runId`、`hostRunId`、`deviceProfile`、`marks`、`metricsSummary`、`series`、`events`、`integrity` 等字段。

`JudgeSpec` 用窗口内 median 与显式绝对阈值比较，默认样本下限为 10；基线用于可比性和参照，并非自动计算百分比回退门槛。`warn` 不会把最终 passed 改为 failed；缺指标、样本不足、基线缺失或档案不匹配为 `not_tested`。原型按此表达，不把缺失值画成 0。

`DeviceGate` 是短事务准入；UI 的 `DeviceSession` 持有设备租约并串行执行。联动应由宿主注入共享准入与调度，不能让独立性能进程与 UI 长租约竞争而宣称可并行。`CollectSession.mark` 已存在，但当前 `PerfKit.run_collect` 内部是计时等待，没有公开 UI 步骤回调：联动打标、平台队列与取消/恢复均需要额外编排。原型“UI + 性能联动”明确标为待接入设计。

性能曲线来自停止后归档/组装报告。本轮未查到面向平台的实时性能推送服务，因此原型展示采集进度与完成后的曲线，不把模拟曲线标为真实实时采样。GPU 为设备相关补采，电量映射有缺口；网络指标需说明全局范围，字典中 `kbps` 与原始 KB/s 注释需在正式接入前校准，本原型主图使用 PSS/CPU/FPS，避免混淆网络单位。

## 界面与交互设计

设计受众：需要执行 APP 回归、分析性能回退、复现失败的测试工程师。默认演示应用为“星河 App”，不是从私有业务中抽取的数据。

视觉：白色工作区 `#FFFFFF`、冷灰画布 `#F5F7FA`、深蓝文字 `#243047`、动作蓝 `#4263EB`、通过绿 `#15836B`、风险琥珀 `#A66A13`。字体采用本机 Segoe UI / 微软雅黑，无远程字体依赖。左侧固定平台导航；主区左对齐，以模块工作区、设备状态和证据为中心。避免装饰性大图，强调不同状态和数据口径。

布局校验：仅放统计卡无法表达两个现有组件的工作方式，因此主屏同时呈现两个模块入口、最近运行、待处理事项和设备资源；调试页以手机画面、UI 树、定位描述三栏组织，性能页以指标、曲线、门禁和基线组织。导航与内容均响应式。

```text
平台导航        应用上下文 / 原型标识
工作台          待处理风险与任务摘要
APP UI 自动化   模块入口 / 最近任务 / 设备资源
APP 性能测试
设备中心        共享资源，查看 / 占用状态
任务中心        新建 → 排队 → 执行 → 完成 / 失败 / 取消
报告中心        UI 失败证据 / 性能门禁与对比
接入说明        代码事实、平台缺口、开源参考
```

原型交互：页内导航与浏览器前进后退、任务搜索和筛选、新建任务向导、设备选择校验、显式推进模拟执行、取消和重新运行、报告联动、性能指标与基线切换、门禁阈值演示、设备占用限制、冻结拾取、图像模板演示、报告 JSON 下载。局部浏览器存储仅保存演示任务/阈值/模板，不保存或读取凭据；若浏览器禁用存储则退化为当前会话。

首期没有做：后台执行服务、真实设备连接、APK 上传安装、云设备供给、多租户权限、CI 集成、AI 自动编写用例和 iOS。不能仅凭原型中的模拟操作认定这些功能已存在。

## 打开方式

双击同目录 `index.html`，无需构建、联网、依赖或服务。建议用桌面浏览器。浏览器中的“交互原型 / 演示数据”标识始终保留；任务按钮只改变本地演示状态。正式开发前先确认信息架构和操作路径，再确定宿主适配层的范围。

## 本轮交互验收与限制

2026-10-25 使用内置浏览器在 `http://127.0.0.1:8877/index.html` 验收；使用项目 `.venv/Scripts/python.exe`（3.12.10）提供只绑定回环的静态预览，不启动两个项目的执行服务。

已验证：

- JavaScript 语法检查；页面没有外部脚本、样式、字体或网络数据接口依赖。
- 工作台、UI、性能、设备、任务、报告、接入说明七个页面可访问。
- 新建任务的空白名称校验、采集时长边界、模块切换、配置确认；离线/未授权设备不可选。
- 排队 → 执行 → 完成报告；模拟失败；取消；重跑生成新编号并保留原记录；搜索空态与刷新后任务持久化。
- 设备占用时直接控制禁用且采集检查被拦截；空闲设备冻结快照、拾取节点、验证定位描述。
- 同名图像模板新增 v2，原 v1 仍保留。
- 性能基线跨档案与样本不足显示未判定；GPU 不支持显示缺失原因；CPU 告警超标不阻断；规则试算不覆盖原始失败结论。
- 未授权设备调试按钮禁用；失败截图示意、异常和 UI 树可浏览。
- 390 × 844 手机视口的导航与工作台/性能页，无文档横向溢出；最终恢复默认桌面视口。检查过程中页面未记录 JavaScript error。
- 报告导出函数在 Node VM 中验证了 UI 失败、性能失败、样本不足、联动通过四类 JSON 内容、文件名与下载触发逻辑。

限制：浏览器工具没有捕获到下载完成事件，因此不声称自动化验证了文件最终落入下载目录；原型已实现标准 Blob 下载。内置浏览器安全策略阻止 `file://` 导航，因此未通过该工具实测双击打开；交互验证使用上述回环 HTTP 地址，离线能力另以零外部依赖检查支持。未执行两个现有组件的真机测试，也未把历史测试日志当作本轮证据。

目录附 `preview-overview.png` 和 `preview-performance.png` 作为本轮浏览器渲染快照。浏览器内可保留名为“验收演示”的模拟任务，文件自身的初始种子数据仍是 8 条。

交付前发现另一个工作流已把性能仓库 HEAD 推进到 `8c8c642`；与最初 `80aaffb` 对比，变更集中在 SoloPi APK、弹窗/记录处理、构建与切换文档，`mobile_perf/kit.py`、`cli.py`、`judge.py`、`gate.py` 无差异，本文依据的 Python 接入边界不变。UI 仓库另出现未跟踪的 `tests/unit/test_upstream_channel.py`，本轮未修改它或原有三个未跟踪文件。`docs/platform-prototype/` 命中现有 Git 忽略规则；本轮没有修改忽略规则或执行提交。
