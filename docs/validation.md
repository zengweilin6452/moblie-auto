# 验证记录(工单 13;2026-10-24)

## 环境

| 项 | 值 |
| :--- | :--- |
| 主机 | Windows 10.0.26200 x64,Python 3.12.10 |
| 包 | mobile-auto 0.1.0(可编辑安装 + wheel 干净环境双验证) |
| 核心依赖 | uiautomator2 3.7.1 / adbutils 2.12.0 / Pillow 13.0.0 |
| 图像 | opencv-python-headless 5.0.0 / numpy 2.5.4 |
| 网页 | fastapi 0.161.1 / uvicorn / websockets |
| 设备 | Xiaomi 2206123SC,Android 14(SDK 34),USB 序列号 749622c9(已授权) |
| 演示 App | 系统设置 v14/vc34,sha256 登记 docs/demo-app.md |

## 命令与结果

```text
python -m ruff check src tests examples           → All checks passed!
python -m pytest tests                            → 248 passed, 12 skipped(设备用例无 --mobile-config 时跳过)
python -m pytest tests/unit tests/contract tests/integration -q
                                                  → 248 passed(plan §12 口径;contract 7 项 15 方法核对;
                                                    integration 1 项真实两进程租约争用)
python -m pytest tests/device -m device --mobile-config tests/device/local.toml
                                                  → 8 passed, 1 xfailed(故意失败取证验证)
python -m build --wheel                           → 成功;static/examples/samples 齐全
干净 venv(中文+空格路径)安装 wheel → doctor 退出码 0;
  pip check(含 image,viewer extras)→ No broken requirements;
  native_flow.py / image_flow.py 真机示例通过;模板资产迁移加载 12 个模板 sha 校验通过
mobile-auto doctor(真实运行)→ 环境可用,列出已授权设备
mobile-auto serve + 浏览器验收 → 实时预览帧/UI 树/快照/拾取候选/模板制作渲染正常
python -m pytest tests/device/test_reliability_30x.py -m device --mobile-config ... -rA
                                                  → NATIVE 30/30 PASS,IMAGE 30/30 PASS
```

## 真机 30 次重复逐次归因(plan 12.1 口径)

- 原生流程(冷启动 → wait_for WLAN → 点击 → 子页断言 → get_text → 截图):
  30/30 PASS,单轮 3.1–3.5 秒,**零失败**。
- IMAGE 流程(冷启动 → wait_for 图像目标 → 图像事务点击 → 子页断言):
  30/30 PASS,单轮 3.0–3.5 秒,**零失败**。
- 过程中归因记录:首轮 IMAGE 30x 全部 FAIL(30/30),归因为**测试制作模板时
  页面未就绪**(goto 后未 wait_for 即截图,模板内容与主页不符)——测试准备
  问题,非工具缺陷;加入 wait_for 就绪后 30/30 通过。该轮不改变
  "零未解决工具原因失败"的结论:失败原因明确、修复后全过、驱动行为一致。
- 既有已知设备特性(不属工具失败):小米 ROM 偶发空 UI 树 dump(u2 内部
  重试可恢复);设备空闲息屏由测试夹具唤醒。均登记于 docs/demo-app.md。

## 三类证据(spec 决定 Q4,互不替代)

1. **真机操作证据**:演示 App 系统设置。原生流程 30/30、IMAGE 流程 30/30、
   操作矩阵(五定位/输入/属性/滚动/按键/句柄过期)、失败取证闭环(xfail
   故意失败 → 证据落盘 → 清单登记)。本轮全部记录于本文件与测试输出。
2. **图像算法证据**:固定合成样本 17 项(坐标精确/排序/颜色验证/拒绝路径)
   + 真实正负样本 12 组(tests/fixtures/images/,来源与校准登记于其 README)。
   与驱动解耦,可独立运行供内网引擎对照(docs/integration.md §7)。
3. **取消与竞态证据**:模拟设备(FakeBackend)确定性测试——"匹配完成后、
   点击发送前取消"不发出点击;已发送后取消结果记 unknown 不重放;排队
   任务可取消;FAULTED 拒绝操作且取证降级;两进程租约争用立即 busy;
   close 有界排空超时保 FAULTED 与租约;session fixture 跨事件循环复用。

## 未覆盖范围(如实声明)

- **extract_list**:未实现。真实 ListItemSchema 与行为样例未获得,不伪造
  兼容 schema;内网绑定门槛见 docs/integration.md §6。
- **内网集成**:真实 Port 枚举映射、异常表、marker 默认值、内网 Python
  版本 wheel 验证——全部待内网方提供输入;本记录仅声明本地验收通过。
- **演示 App 未覆盖场景**:列表滚动加载、嵌套滚动容器、WebView 内控件、
  自绘非 EditText 输入框、长按手势、多窗口/分屏。深场景演示 App 另选应用
  并重新登记(docs/demo-app.md)。
- **完整离线安装包**:依赖轮子离线库(--no-index)装机流程已写入
  docs/offline-install.md 但未在本机断网演练;内网接入时执行。
- **opencv-python(非 headless)共存**:内网既有 cv2 环境的组合验证步骤
  成文(docs/integration.md §8),未在本机复现内网组合。
- **网页多客户端并发**:WS 上限 2 客户端有单元测试;真人多端并发未实测。
- **pytest-xdist 并行**:明确不支持(spec Out of Scope),未测。

> 交付方式备注(2026-10-24):经用户决定,本项目不打包 wheel,接入直接使用源码;
> 下文 build/wheel 验证保留为能力记录,不构成交付要求。

## 验收结论

- **本地验收:通过**。六项工单验收链(01–13)全部完成;lint/单元/契约/
  集成/真机/打包/干净安装/30 次重复全部达到 plan §12 命令口径。
- **内网集成:待验证**。绑定层实现、真实 Port 契约核对、引擎对照、
  OpenCV 共存与离线装机,须按 docs/integration.md 与 docs/offline-install.md
  在内网环境执行后另行出具结论。

## 图像识别 V2(2026-10-24 追加)

工单来源:docs/image-v2-plan.md(审查批准,决定记录于该文档第八节)。

```text
python -m pytest tests/unit -q                          → 230 passed, 1 skipped
python -m ruff check src/mobile_auto scripts/bench_image.py → All checks passed!
python scripts/bench_image.py phases                    → 同分辨率 1080x2340 端到端 86ms(v1 277ms)
python scripts/bench_image.py samples                   → 8 正样本全过、4 负样本全拒(分门数值记录于 image.py)
python scripts/bench_image.py crossres                  → 0.85/1.15/1.25× 真实样本命中,150–340ms/次
```

- 分期提交:603b726(P1 向量化)/ 7a1413d(P2 模板缓存)/ 02527a7(P3 颜色门)/
  c40750c(P4a 有界多尺度)/ f2292f1(基准脚本)。
- 行为兼容:同分辨率路径与 v1 决策一致(现有样本回归全绿);P1 经 60 轮
  随机分数图新旧实现等价性验证(22753 峰值零差异)。
- 真机 IMAGE 用例未随本次重跑(引擎层回归以单测与真实样本覆盖;
  30 次重复基线见上文,后续真机复验建议在 P4b 决策时一并执行)。

## P1 体验闭环(2026-10-24 追加;工单来源 .scratch/p1-experience-loop/)

### 命令与结果

```text
python -m ruff check src tests scripts examples      → All checks passed!
python -m pytest tests/unit tests/contract tests/integration -q
                                                    → 295 passed, 1 skipped(node)
node tests/unit/dual_pane_logic.test.js             → 6 tests passed
python -m pytest tests/device -m device --mobile-config tests/device/local.toml
                                                    → 24 passed, 1 xfailed(P0+P1 全量;
                                                      含 scrcpy 通道/流化/双通道/注入/
                                                      插件取证/拾取 e2e 回归)
干净 venv(.scratch/p1-venv)-e ".[image,viewer,stream,dev]" → pip check 通过;
  fetch_scrcpy_server.py 幂等;tests/unit 295 passed
```

### 实测记录(Xiaomi 2206123SC,Android 14,USB)

| 指标 | 值 | 口径 |
| :--- | :--- | :--- |
| server 启动+连接 | ~1.0s | 工单 01 spike,含 jar/so 推送 |
| 预览帧率 | ≥8fps(10s 采样达标) | 视频流档 100ms 间隔,丢帧不堆积 |
| 杀 server 回退 | ≤3s 出现 u2 回退帧 | 自动恢复 ~5s(退避 2s + 重建) |
| CAPTURE 截图 | 原生 1080x2400 PNG,10 次均值 122ms;一致性基线时延对比 u2 中位 141ms vs scrcpy 62ms | 同屏同焦点 |
| 匹配一致性 | 数量一致、边界差 ≤1px、置信度差 ≤0.005 | 设置首页 WLAN 样本,confidence 0.80 |
| 注入 | tap 打开 WLAN 子页、back 返回、swipe 画面变化 | 业务状态断言 |
| 失败取证 | frame_source=scrcpy-video,section 附加 | 子进程故意失败用例 |

### 关键实现事实(复现排障用)

- wetest-scrcpy==0.4.0 wheel **缺 scrcpy-server jar**(上游打包回归);
  jar/libjpeg-turbo 自 0.3.8 wheel 提取(src/mobile_auto/scrcpy_assets/,
  SHA256 与来源登记 PROVENANCE.md,提取脚本幂等)。
- server(wetest 版 3.3.2.11)硬编码从 `/data/local/tmp/udt/libjpeg-turbo.so`
  dlopen;缺失时 CAPTURE 空帧并 ~60s 捕获停摆。通道启动推送 so 到该路径。
- GET_SCREENINFO 存在启动竞态(偶发返回视频缩放尺寸 488x1080);
  screen_size 以 `wm size` + `get_rotation` 为权威。
- 息屏时编码器停帧(视频流显示末帧)+ u2 dump 挂起(采集有界超时);
  调试场景由夹具唤屏,登记为已知限制。
- scrcpy server 进程唯一 socket 前缀(ma-<pid>-<seq>);跨进程各自持有
  server,生命周期随会话/管理器回收;空闲 30s 自停。

### 未覆盖范围(如实声明)

- 浏览器端 h264 解码(WebCodecs/MSE):未采用(设计决策:服务端解码 JPEG
  over WS),不构成承诺。
- 多浏览器兼容性矩阵:仅本机默认浏览器人工验收预览渲染。
- wetest-scrcpy 许可证:PyPI 元数据未填 License、无公开源码仓库;
  本地开发可用,内网接入前须合规复核(offline-install.md 待核查项)。
- 直接控制的并发压测(节流上限行为)仅有单元级验证。

## P4b 采集位置先验 + 真机复验(2026-10-25 追加)

工单来源:docs/image-v2-plan.md(P4b 原缓行,本次实施);提交 fb1105b。

```text
python -m ruff check src tests scripts examples     → All checks passed!
python -m pytest tests/unit tests/contract tests/integration -q
                                                    → 全部通过(exit 0)
python scripts/bench_image.py prior                 → 全屏 80ms / 先验命中 39ms
                                                      / 未命中回退 +1ms
python -m pytest tests/device -m device --mobile-config tests/device/local.toml
                                                    → 24 passed, 1 xfailed
python -m pytest tests/device/test_reliability_30x.py -m device --mobile-config ...
                                                    → NATIVE 30/30 PASS,
                                                      IMAGE 30/30 PASS,零失败
```

- 图像 V2 后真机 30 次基线(此前 validation 记录的"IMAGE 用例未随 V2 重跑"
  项已闭环):NATIVE 3.1–3.8s(均值 3.4s);IMAGE 2.4–3.1s(均值 2.6s,
  较 v1 基线 3.0–3.5s 提速,先验路径生效——测试模板经 studio 同源换算
  携带 capture_center,30 次全部走预测区域命中)。
- bench_image.py 修复模块重排后的旧导入路径(mobile_auto.image →
  mobile_auto.imaging.engine)。
- 未覆盖:P4b 对"预测区域外出现同图实例"的真机场景未做专门用例
  (单测覆盖几何与回退语义)。

## Beta_Academy 冷启动稳定化(2026-10-25)

对象:Beta_Academy V1.9.77(com.sinosoft.trainapp),真机 749622c9。

- **定位结论**:冷启动落点由 App 会话是否有效决定,不是随机竞态。
  会话有效时 15/15 次冷启动(10+5,含重启重试路径)全部直达首页,
  goto('')+wait_for_load(desc='language') 约 1.2–1.3s 就绪;
  落在登录页与 2026-10-24 的观测为会话过期所致,重启重试救不了
  (服务端状态),须人工登录一次(图形验证码不可自动化)。
- **分类判据(语言无关)**:首页 = desc=Search(登录页没有)+ desc=language;
  登录页 = 多个 EditText(账号/密码/验证码)且无 desc=Search。
  注意首页搜索框也是 EditText,不能以 EditText 存在性分类。
- **加固落地**:docs/snapshots/verify_coldstart_recovery.py —— goto 后
  先清理 MIUI 系统权限对话框(点「拒绝」保持既有授权状态)再 wait_for_load;
  登录页落点一次重启重试,仍登录页则明确报错要求人工登录。
- **新发现的稳定性风险**:导航中随机弹 MIUI 系统权限对话框
  (com.lbe.security.miui,媒体/通知),挡住 dump 与锚点等待
  (本次实测曾导致 wait_for_load 超时);自动化流须带对话框清理步骤。

## Beta_Academy 深场景可行性(2026-10-25)

脚本:docs/snapshots/verify_deep_scenarios.py(证据 dump 同目录 deep_*.xml,
结论存 deep_scenarios_verdict.txt;截图不入库);全部操作走 MobileAdapter 契约面,
底层探针单独标注。

| 场景 | 结论 | 依据 |
| :--- | :--- | :--- |
| 列表滚动加载 | 部分可行 | 知识库列表可滚动;当前账号仅 5 项、数据不足以触发分页;工具能力(scroll+计数断言)成立,分页断言需数据充足时复验 |
| 嵌套滚动容器 | 结构存在,定向操作不可行 | 首页 ScrollView 内嵌两个横向容器(y=[0,637]/[983,1170]);契约 scroll 固定屏幕中线 y=1200 手势,内层均不在射程;契约无坐标级手势 API(既定决策:真实场景需要时再扩展 adapter 面) |
| WebView | 可行 | 设置页「问题反馈」打开 android.webkit.WebView(证据 deep_s3_target_1.xml);其余已探查页面均为原生 Compose 容器 |
| 长按 | 不可行(契约) | 15 方法无 long_press;底层探针:同点 0.8s swipe 注入无报错,但被 App 识别为普通点击(进入目录),不能替代长按;u2 原生 long_click 未暴露到本包后端 |
| 多窗口/分屏 | 不可行(契约) | 触发分屏需最近任务里拖拽/长按卡片,契约无坐标手势与长按;press_key('recent') 为可达最远点;App 标志 RESIZEABLE_VIA_SDK_VERSION(支持分屏≠可经工具触发);adb shell 路径(am stack/windowingMode)不属于契约,未采用 |

其中"不可行"均为**当前契约面**的边界(坐标手势/长按/分屏触发 API 不在
15 方法内),不是 App 或驱动的硬限制;按 adapter API 契约决定
(等真实业务场景需要时按需加 adapter.device.* 扩展面),如需启用应走扩展评估,
不静默加面。

## adapter.device 手势扩展面(2026-10-25,同日落地)

上述"深场景验证"即触发扩展评估的真实场景;当日按 2026-10-24 契约扩展
决定实施 `adapter.device` 命名空间(绑定层不映射、契约测试不涉及):
`tap(x, y)` / `swipe(x0, y0, x1, y1, duration_s)` / `long_press(x, y, hold_s)`,
坐标级手势原语,返回实际使用通道。通道选择按调用 `source` 参数
(auto/scrcpy/u2):auto 默认 scrcpy 控制流优先(原始 socket 写入),
通道不可用或首写(DOWN)失败整体回退 u2;scrcpy 强制模式故障如实报错。

前置验证(2026-10-25,探针 docs/snapshots/probe_u2_longclick.py):
u2 `long_click` 与 `touch.down+up` 均为框架级长按(MIUI 桌面长按进入
编辑模式,「小部件」标记);同点 swipe 拼长按不可靠(被识别为普通点击),
故 u2 回退用原生 long_click。搜索框 EditText 无粘贴菜单是 MIUI 剪贴板
限制,不是注入失败。

正确性不变量:手势为多写序列,DOWN 后任何失败先经 u2 touch_up 补发
抬指再报错(两通道同经 InputManager 注入),不重放整个手势。

```text
python -m pytest tests/unit/test_device_ops.py -q   → 15 项通过
  (auto 回退/首写整体回退/DOWN 后 UP 收尾不重放/source 钉死/参数校验)
python -m pytest tests/device/test_device_ops_device.py -m device ...
                                                    → 2 passed
真机实测:
  密集 6 连滑(0.25s/次)墙钟:u2=5.23s scrcpy=2.89s(省 2.34s,快 45%)
  长按双通道(scrcpy/u2)均触发 MIUI 桌面编辑模式
  坐标点击/滑动在演示 App(系统设置)行为与契约方法一致
```

深场景复验(verify_deep_scenarios.py 更新,Beta_Academy):
- S2 嵌套滚动:**可行(经 device.swipe)**——课程页分类横条(y≈402)
  坐标手势命中,分类滚动可见;首页内嵌容器为静态布局(与手势无关)。
- S4 长按:**可行(经 device.long_press)**——框架级长按双通道真机
  验证;知识库条目长按 App 未定义菜单(行为=进入目录同点击,该 App
  此处无长按交互),长按能力以系统级判据为准。
- S5 多窗口/分屏:仍不可行——MIUI 最近任务为专有 UI,卡片坐标不可
  稳定定位,脚本化不可靠;留待真实需要再评估。
- S1/S3 结论不变(部分可行/可行)。

## P2-01 scrcpy ServerSupervisor 统一保活与优雅回收(2026-10-25)

环境:Windows/Python 3.12/真机 749622c9,wetest-scrcpy 0.4.x,commit 见工单。

- 单元:tests/unit 全量通过;新增 tests/unit/test_supervisor.py 15 项(引用计数/空闲自停/退避重建/并发启动守卫/atexit 限时取锁)。
- 真机:tests/device 全部通过(含 test_reliability_30x);test_kill_server_falls_back_and_recovers 的 wetest 线程告警经 fb1105b worktree 与 stash 交叉验证为 c2d1383 时点既有现象,非本工单回归。
- 残留验收(pgrep 方括号写法排除 shell 自匹配):
  - 驱动通道启动后不 close 直接退出(atexit 兜底):设备端 `ma-` server 残留 0;
  - serve 经 CTRL_BREAK 模拟 Ctrl+C:uvicorn 优雅停机日志完整,finally registry.shutdown() 执行,残留 0(退出码 3 为解释器退出阶段第二个控制台事件产物,非清理失败)。
- 已知边界:硬终止(taskkill /F)后的残留由工单 02 自愈路径处理,本工单不覆盖。

## 契约路径注入透明双通道(2026-10-25,device.* 之后第二步)

device.* 落地后,把契约操作的注入也统一到双通道(契约面零变化):
配置 `injection_source`(auto/u2/scrcpy,默认 auto,语义同
screenshot_source)统辖契约 scroll 的手势、图像点击事务的 tap 发送、
press_key 的 back/home,以及 adapter.device 的 auto 默认;策略与降级
窗口由 `session.inject_gesture` 提供并与截图双通道**共用失败计数**
(同一 scrcpy 通道,一个健康状态):连续 3 次失败后 60s 窗口内 auto
直接走 u2,到期再探测恢复。手势安全不变量与 device.* 同源(DOWN 后
失败先 u2 touch_up 抬指再报错、首写失败整体回退、不重放)。

```text
python -m pytest tests/unit/test_device_ops.py -q   → 23 项通过
  (新增:契约 scroll 走通道/首写整体回退/配置钉死/session 注入路径/
    press_key 路由/降级窗口开启与到期再探测)
python -m pytest tests/unit tests/contract tests/integration -q → exit 0
python -m pytest tests/device -m device --mobile-config ...      → 全部通过
  (含 30x NATIVE/IMAGE 复验:图像点击发送路径已透明走通道)
```

真机实测(复测稳定):
- 契约 4 连滑(scroll down/up ×2,透明 auto 注入)墙钟 **2.05s**
  (u2 同序列按均摊 ~0.87s/op 估算约 3.5s,提速 ~40%);
- 密集 6 连滑 device.swipe:u2=5.25s vs scrcpy=3.03s(省 2.2s,42%)。
- 已知边界:手势插值位置 u2 在设备侧、scrcpy 在主机侧,fling 惯性
  可能略有差异;时序敏感用例可 `injection_source = "u2"` 钉死。
- dump/查找/输入等取数类操作不迁(scrcpy 无 accessibility 通道;
  input_text 中文 scrcpy 不支持);u2 侧空 dump 等既有风险不变。

## P2-02 scrcpy 残留 server 自愈(2026-10-25)

- 枚举机制真机实证:wetest server 将进程名(comm)设为 socket 前缀,`grep /proc/[0-9]*/comm` 一条命令枚举;cmdline 方案不可行(java 进程 cmdline 不含启动参数)。
- 组件级真机验证:伪造 ma-99999-1(死宿主)→ 扫描命中 → kill → 进程与 abstract socket 均消失。
- 单测 9 项覆盖 start 失败→自愈→重试一次、降级模式、同进程活跃通道保护;全量单测与真机回归(含 30x)通过。
- 重要发现:该设备编码器并发 ≥7;taskkill /F 宿主与杀 adb server 两条路径实测均不产生残留(adb 链路逐级清理)——自愈为第三道防线,完整 e2e 触发在本设备无法自然复现。

## P2-03~08 测试平台过渡主体(2026-10-25)

环境:Windows/Python 3.12/真机 749622c9;提交 c143d36(04)/29e984d+9a50e42(05)/44e1a02(03)/7e600a4(06)/39d598d(08)/86c2df6(07)。

- 04 租约持有者信息:实测 Windows 区域锁持锁期间锁文件任何句柄 I/O 均被拒(含未锁区域),持有者信息改存伴生 holder.json(单写者);probe_held 试锁判定占用。单测 10 项 + 集成回归通过。
- 05 设备池:租约目录即协调器;预获取租约经 lease_factory 注入会话消除竞争窗口;真机单设备池全链路通过(真租约→会话→scrcpy 截图→runs/main.json→holder→释放回落)。xdist 双设备验收受设备数限制未执行(单设备池等价现状);pytest-xdist 未入 dev extras,内网 CI 接入时补装。
- 03 --debug-viewer:真机 pytest 运行期间 http://127.0.0.1:8765 可访问(设备列表正确),会话结束自动关闭、退出码 0;SO_REUSEADDR 在 Windows 会连监听端口也绑定成功,端口探测必须不设。
- 06 设备档案:真机返回 Xiaomi·2206123SC·Android 14·1080x2400;缓存命中不再探测。
- 07 占用/运行态:serve 与 pytest 共用同一 --mobile-config 时,运行期间 /leases 可见持有者、/runs 可见当前用例,结束回落空闲/finished。数据源一致性前提:两者共用同一 artifacts_dir。
- 08 证据浏览:--mobile-evidence 真实取证(scrcpy-video 帧源)后清单/详情/248KB 画面 PNG 全链路可浏览;白名单防穿越单测覆盖。
- 附加发现:tests/device/test_device_ops_device.py 长按桌面用例当前失败——(540,1500) 被桌面图标格占据(长按出快捷菜单而非编辑模式),adb 原生 input 同样复现,属设备桌面状态漂移而非代码回归;建议用例所有者改为动态寻找空白区域。

## P2-05/07 补验:xdist 双设备真机验收(2026-10-25)

设备:7496123SC(749622c9)+ 25102RKBEC(84cb2c4e);pytest-xdist 3.8.0(验收环境临时安装,不入 dev extras)。

- `-n 2 --mobile-devices=749622c9,84cb2c4e --debug-viewer`:两用例并发全过(rc=0),无 409/DeviceBusy 干扰。
- 运行期 viewer /leases 实时可见双机同时被持且持有者 worker 互异(实测映射 gw0↔84cb2c4e、gw1↔749622c9,两次运行分配顺序不同——非阻塞抢占的预期行为);/runs 的 worker→serial 与占用一致。
- 产物隔离:artifacts/xdist-gw0、xdist-gw1 各含本 worker 写入的截图;runs/gwN.json 收口 finished 且 serial 互异。
- 结束后 probe_held 双机回落空闲;--debug-viewer 在 controller 正常并行,会话结束自动关闭。
- 环境备注:并发场景下亮屏看护勿用 u2 RPC(jsonrpc 未就绪报 RPCUnknownError),改 adb input 唤醒。

## scrcpy 上游迁移(Android 17 兼容)——feat/scrcpy-upstream 分支(2026-10-25)

spec:.scratch/scrcpy-upstream/(协议对照 v5.0.1 源码逐项核实);提交 632c12a→9892bfc。

- **Android 17(84cb2c4e,Redmi 25102RKBEC)**:UpstreamChannel 启动 1.0s 就绪(含旋转自适应 496x1080),60s 存活 ~19.9fps(max_fps=20 满帧,反超 wetest 10fps),home/tap/swipe 注入生效,kill server 0.1s 内检测不健康,stop 零残留(ps 计数;pgrep -f 有 shell 自匹配假象,以 ps+字符类规避为准)。
- **Android 14(749622c9)**:同项通过(486x1080 ~10fps,该机 HUD 限频);双机 viewer 并发出流(默认 upstream)。
- **自愈双前缀**:wetest(comm=ma-*)与 upstream(cmdline scid=)残留同场混扫,单测 12 项;真机伪造死宿主残留(0xdead)与同 scid 冲突场景均自愈恢复。
- **实现期真机实证修正**:v5 默认 send_device_meta=true 会先发 64B 设备名块(显式关闭);CONFIG 包须设 extradata 而非直接 decode(InvalidDataError;单包失败降级丢包续流);上游 server 不因 stdin EOF 退出,按 scid 记 pid 显式 kill。
- 默认 stream_backend 已翻转为 upstream;wetest 保留为回退(显式 stream_backend="wetest")。
- 遗留(2026-10-25 更新):capture_png 分辨率语义已随 PRD §8.5 修订(upstream=流分辨率,全分辨率取证走 u2);wetest 通道删除工单仍待内网设备矩阵验证。

## reuse-smart-dri 评审:双机门槛降级与跨密度替代(2026-10-25)

- 现状:84cb2c4e 当前不在 `adb devices` 列表(仅 749622c9),双机门槛无法执行;上一节的双机记录仍是有效历史依据,不作废。
- 降级:`pytest tests/device` 以 749622c9 单机 + fake 通道通过为准;84cb2c4e(Android 17)上线后补跑双机,并补一次 viewer 并发出流与注入回归。
- **跨密度验收替代**:不依赖"另一台同分辨率不同密度设备",改用同机 `adb shell wm density 480` 覆盖(记录原值,`finally` 里 `wm density reset`)采两帧 + 两棵 UI 树。判据:①内容仅均匀放大(树结构/节点数一致)②多元素拟合尺度 ≈ 480/440 = 1.0909 ③资源桶未切换(sw392dp 保持)。
- 该探针同时是 `.scratch/reuse-smart-dri/issues/02-density-aware-scaling.md` 的开工闸门;三项任一不过则 02 降级为"模板限同 density 设备复用"的文档约定,不当场加宽尺度硬试。
- 2026-10-26 更新:本轮环境无设备,闸门未执行;经确认先实现算法(密度路径仅在模板 meta 带 density 时启用,旧模板行为不变),闸门与跨密度真机验收见下文「工单 02」小节。
- 待补项(设备上线后):双机 `tests/device` 复跑;84cb2c4e 的 viewer 并发出流与注入;出现 `wm size` 覆盖/折叠屏机型时的坐标基准反例(见 04)。

## 动作流水(工单 01,2026-10-26)

- **单元/契约验证**:`tests/unit/test_journal.py` 覆盖字段与脱敏、`ok`/
  `not_found`/`error`/`unknown` 结局、发送前/发送中取消区分、嵌套公共调用
  只写一行、写失败禁用、32 MiB 截断、无 journal 时零文件,以及
  `meta.journal_summary` 消费。
- **真机验证:待补**。本次实现环境无可用设备,未录 Beta_Academy 真机流水;
  接入后按工单 01 测试节补录:
  1. 带 `--mobile-evidence` 跑固定公共调用序列(`goto`/`is_visible`/`click`/
     `scroll`/`device.tap`/`back`),`<artifacts>/actions.jsonl` 行数 = 序列
     公共调用次数(xdist 下在 `xdist-<worker>/actions.jsonl`);`wait_for`
     轮询全程只一行。
  2. 不带 `--mobile-evidence` 时产物目录不产生 `actions.jsonl`。
  3. viewer 侧 `<artifacts>/viewer/<serial>/actions.jsonl` 行数 = 注入请求
     次数,tap/swipe 行含实际 `channel` 与 `coordinate_basis`。

## 坐标基准保鲜(工单 04,2026-10-26)

注入坐标必须与设备当前显示基准一致;此前 `Channel.refresh_screen_size` 在 `src/`
内零业务调用者,旋转/折叠后 viewer 与 `channel.touch` 的 clamp 都按旧尺寸点错位置。

- **调用时机**:`DeviceSession.inject_gesture` 在拿到通道后、首写(DOWN)之前调用
  `ensure_coordinate_basis(backend, channel)`;viewer `/api/v1/input/touch`、
  `/input/swipe` 在 `_inject` 的设备队列任务内先保鲜再取 `channel.screen_size()`
  换算归一化坐标。阻塞的 `wm size`/刷新不得在事件循环线程执行。
- **校验内容**:通道缓存尺寸 vs 后端实时 `wm size`(`U2Backend.window_size`);
  不一致且通道可刷新 → `refresh_screen_size()` → 重读 → 注入。
- **节流**:同一会话、同一通道对象 1 秒内最多校验一次(含一次 `wm size` 往返);
  通道对象变化(监督器重建)立即重校验。正常未旋转流程因此不增加 shell 往返。
- **通道能力**:`ScrcpyChannel.can_refresh_screen_size = True`(重探 `wm size` +
  rotation);`UpstreamChannel.can_refresh_screen_size = False`。
- **upstream 限制(不得静默假绿)**:上游通道 `refresh_screen_size` 是接口同形的
  空实现,`screen_size` 来自视频会话元包,可能被 `max_size` 缩放,旋转时由泵线程
  在下一元包自动更新。因此对不可刷新通道只比较宽高比:比例一致视为同一显示区的
  视频缩放(`consistent=true`);比例不一致如实记 `refreshed=false,
  refreshable=false, consistent=false`,不调用空实现、不改道、不重放注入。
  若真机出现 upstream 旋转后基准迟迟不更新,再评估 `dumpsys display` / 显示区偏移
  (spec 前提崩塌 4),当前不做。
- **source 语义**:`auto` 实际走 u2(含降级窗口)与显式 `source="u2"` 都不改道
  注入通道。降级窗口内直接走 u2 的路径不探测通道(降级原因即通道不可用);
  显式 `u2` 若已有可用通道,同样做尺寸一致性校验并记录基准,没有可用通道时
  **不为校验而启动通道**,如实记 `channel=null`/`refreshable=false`/
  `consistent=false` + `reason`。显式 `source="scrcpy"` 校验但**不改道**。
- **观测**:journal 注入行 `coordinate_basis = {channel, backend, refreshed,
  refreshable, consistent, checked_at[, refresh_error][, reason]}`;失败证据
  `meta.device.coordinate_spaces` 为同一快照(无基准可记时为 `null`)。
- **已知口径**:`ScrcpyChannel._resolve_screen` 只取 `wm size` 第一个 `WxH`,不处理
  `Override size:` 行,与工单 02 的 density 口径(先 Override)不同;`wm size 1080x2400`
  测试残留会造成一次合法不一致,校验只刷新一次,不循环。
- **真机验证(待补,本轮无设备)**:`adb -s 749622c9 shell settings put system
  accelerometer_rotation 0` → `adb -s 749622c9 shell settings put system user_rotation 1`
  → viewer 连续 10 次点击目标元素应全部命中,journal 出现 `refreshed:true` 行;
  测完按原方向恢复:`adb -s 749622c9 shell settings put system user_rotation 0`
  (原为横屏则按原值),再 `adb -s 749622c9 shell settings put system
  accelerometer_rotation 1` 恢复自动旋转。

## wait_gone / click_until_gone 原语与 index 语义(2026-10-26,工单 05)

- **目标**:把"等某个东西消失/点到它消失"从用例手写轮询变成原语;用例里
  不再出现 `while True: if not visible: break` 的消失轮询。两者为契约外
  便捷方法,绑定层需转发(见 `docs/integration.md` §12)。
- **单元验证**:`tests/unit/test_wait_gone.py` 覆盖 visible→visible→gone 时
  点击 2 次返回 True、预算耗尽返回 False 且点击数 ≈ ⌈timeout/interval⌉、
  `AmbiguousTarget` 透传不重试、IMAGE 路径、非法 `timeout`/`interval`、
  设备错误透传,以及预算耗尽时动作流水只有一行 `outcome="not_found"`。
- **真机验证:待补**。本次实现环境无可用设备,未录 Beta Academy
  "点击关闭弹窗直到消失"(当日无弹窗则用设置页搜索建议下拉替代)场景;
  接入真机后按工单 05 测试节补录返回值语义与耗时。
- **`reset_uiautomator()` 恢复路径不实现**:该恢复是设备写操作(重启
  uiautomator server / 强制重连 u2),可能在他人持有时打断服务,且不在
  契约面。触发条件:同一会话连续多次 `dump_ui` 超时,`is_visible`/
  `wait_for` 在设备仍在线时持续报 `HTTPTimeoutError`,且
  `session.state` 仍为 LEASED。观察方式:失败证据 `meta.not_captured`
  出现 `UI 树取证未完成(HTTPTimeoutError)`,或动作流水连续
  `outcome="error"`(且 `error_type` 为 `HTTPTimeoutError`);此时人工重连
  设备或在 setup 阶段重启 u2,不自动重启。
- **`index` 语义**:`Target.index` 为树遍历序(uiautomator 层级顺序),
  不是视觉位置序;详见 `docs/integration.md` §1 与 `core/selectors.py`
  模块 docstring。

## 模板密度感知缩放(工单 02,2026-10-26)

### 探针闸门:未执行(当前无设备)

0.5 天探针闸门的**真机部分未执行**:本次实现环境无可用设备,下文不含
任何实测密度帧、拟合比或树 diff,也不据此收紧搜索范围。执行机型参考
749622c9(实测 1080×2400 / 440 dpi)。

命令(记录原值;无论中间是否失败,`finally` 必须复位密度):

```powershell
$serial = "749622c9"
$orig = adb -s $serial shell wm density        # 记录 Physical/Override 原值
try {
  adb -s $serial shell wm density 480          # 覆盖到 480,理论尺度 480/440 = 1.0909
  adb -s $serial exec-out screencap -p > artifacts/probe-density-480.png
  adb -s $serial shell uiautomator dump /sdcard/tree-480.xml
  adb -s $serial pull /sdcard/tree-480.xml artifacts/probe-tree-480.xml
} finally {
  adb -s $serial shell wm density reset
}
adb -s $serial shell wm density                # 确认复位(与原值一致)
adb -s $serial exec-out screencap -p > artifacts/probe-density-orig.png
adb -s $serial shell uiautomator dump /sdcard/tree-orig.xml
adb -s $serial pull /sdcard/tree-orig.xml artifacts/probe-tree-orig.xml
```

同一页面(不导航、不滚动、不弹键盘)采两帧两树后回答三问:

1. **内容是否仅均匀放大**:两棵 UI 树结构/节点数一致,同节点 bounds
   按 480/440 等比放大;出现重排(节点增减、换行、隐藏)即为"否"。
2. **尺度是否稳定**:多元素拟合出的尺度是否一致且接近理论 1.0909;
   记录各元素实测比与离散程度(即 `_adapt_scale` 的 span 校准依据)。
3. **资源桶是否切换**:dp 宽度 393→360 是否跨断点,应用是否加载
   `values-sw360dp/` 等另一套资源(布局可能非均匀变化)。

判定与降级路径:

- 三问全过 → 密度路径按当前实现生效;用实测抖动收紧 `_adapt_scale`
  的 span(当前沿用 `_SCALE_SPAN=1.3`,粗扫 0.05 / 细扫 0.01 不收紧,
  理由见 `imaging/engine.py` 模块 docstring)。
- 任一不过 → 本工单降级为**文档约定:模板限同 density 设备复用**,
  不改算法、不当场加宽尺度范围硬试;把探针输出作为降级依据登记在此。

### 合成验证(已完成,不代替真机)

- 跨密度(同分辨率):`tests/unit/test_density_scaling.py` 用同一合成帧
  裁模板、当前帧按 480/440 缩放并居中回原尺寸;旧单尺度路径 0 命中,
  `source_density=440, current_density=480` 命中且中心容差 ≤2px。
- 回归:同密度/同分辨率、仅单侧密度、相对差 ≤2%(440→448)、分辨率
  不同(面积比)四条路径与旧实现逐字段一致;旧 meta(无 density)不
  触发密度路径,也不新增设备调用。
- 负向:真实负样本(`tests/fixtures/images`:目标不在该页、滚动前不可见、
  同形异色 `t-icon-recolor`)在密度路径下仍为 0 命中,阈值未放宽。
- 贯通:`tests/unit/test_density_plumbing.py` 覆盖 `wm density` 解析
  (Override 优先)、会话缓存与 `refresh_density()`、适配器仅在模板带
  density 时读密度、图像句柄重校验共用同一组密度、viewer
  `_query_payload` 复用 `selectors.image_match_kwargs`、快照密度登记进
  模板 meta。store v3 校验见 `tests/unit/test_assets.py`(v2 旧 meta 可
  加载,非法 density 拒绝)。

### 刷新时机与已知限制

- 密度缓存:会话内缓存一次(`DeviceSession.density`);viewer 每次
  采集/验证调用 `refresh_density()`(真实调用点),跟随 `wm density`
  覆盖;适配器只对 meta 带 density 的模板读取缓存,旧模板零额外调用。
- **字体缩放(`font_scale`)未记录**:含文字的模板在字体缩放不同的
  设备上会非均匀失配,密度路径不解决;本工单不实现。
- **响应式重排不解决**:跨 dp 断点导致的重排属探针闸门第 ③ 问,任一
  不过即按上面降级。
- **真机项待补**:跨密度样本(同机 `wm density` 覆盖)与真实负样本
  真机复跑未执行;84cb2c4e(Android 17)当前离线,双机对照仍为待补项。
  现有 `tests/device` 用例在无设备时 skip,未弱化任何断言。


## web-preview-experience(2026-10-28:浏览器直解视频与单操作者控制)

规格:`.scratch/web-preview-experience/spec.md`;工单 01–06。首轮为合成
(单测/集成)验证记录;真机性能与浏览器场景见文末「真机验收(2026-10-29)」。

### 已验证(全部自动化,本轮新鲜证据)

- 单元全量:`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/unit`
  → **622 passed, 1 skipped**(device 标记无真机跳过)。新增:
  `test_videostream.py`(27B 线格式往返/越界拒绝)、
  `test_upstream_subscriber.py`(关键帧门控/溢出/close 解阻塞/代际递增/
  订阅回放 config+session)、`test_viewer_video_ws.py`(严格同源拒绝、
  bad-hello、非 upstream 后端拒绝、config/session/frame 中继、溢出状态、
  通道不健康状态、capabilities 探测)、`test_viewer_gesture_ws.py`
  (同源/凭据、DOWN/MOVE/UP 序列与像素换算、seq 单调、双手势拒绝、
  断连尽力 UP 并释放租约、2s 空闲超时清理)、
  `test_control_injection.py`(重写为凭据流:无凭据 400/错误凭据 403/
  领用幂等/他人 409/心跳续期与 TTL 撤权/节流/租约 409)。
- 前端纯函数 node 套件:`video_player.test.js`(27B 头解析、codec 串
  avc1.PPCCLL 推导、畸形输入拒绝),已挂入 `test_dual_pane_logic.py`。
- ruff:改动文件与新文件 0 告警。

### 环境相关既有失败(与本次改动无关,基线复现确认)

- Windows GBK 控制台下,`test_debug_viewer` / `test_pytest_plugin` /
  `test_pool_plugin` 的子进程输出按 GBK 解码失败;带
  `PYTHONIOENCODING=utf-8` 全绿。基线(stash 本次改动)同样失败。
- `test_debug_viewer::test_start_serves_status_then_stop_closes_port`
  整跑偶发端口残留,单跑必过,与本次改动无关。
- 已顺手修复:`test_dual_pane_logic.py` 的 node 子进程改为显式 UTF-8
  解码(此前 GBK 环境必失败)。

### 未执行范围(首轮)

- 真机性能门槛首轮未测(Chrome/Edge ≥18fps 中位、点击回显 p95 ≤500ms、
  首帧 ≤2s),已于 2026-10-29 补齐,见下节。
- 浏览器端到端交互首轮未经真实浏览器验证;本轮已用 Chrome 156 与
  Edge(Chromium)实测视频档解码渲染与手势点击。
- 旧无凭据客户端的"明确拒绝"已由单测覆盖(400 control-credential-
  required),但无真实旧客户端回归。

## 真机验收(2026-10-29:web-preview-experience)

环境:设备 `749622c9`(1080×2400,USB);后端 `stream_backend=upstream`
(scrcpy-server 5.0.1,`max_size=1080 max_fps=20 video_bit_rate=4000000`);
服务端 `.venv/Scripts/python.exe -m mobile_auto.cli serve --config
tests/device/local.toml --port 8799`;测量探针
`.scratch/web-preview-experience/acc_harness.js`(patch
`CanvasRenderingContext2D.drawImage`,只统计 `#video-canvas`;画面变化用
24×54 降采样灰度均差 >1.5 判定)。

### 门槛结果(Chrome 与 Edge 均达标)

| 项 | Chrome 156 | Edge(Chromium) | 门槛 |
|---|---|---|---|
| 30s 连续动态画面绘制 fps(中位) | 20(mean 20,min 19,max 21) | 20(mean 20,min 20,max 20) | ≥18 |
| 30 次可见变化点击 p95 | 460ms(中位 415.5,max 511.7) | 438.5ms(中位 408.5,max 464.7) | ≤500 |

- fps 上限即服务端 `max_fps=20`;两引擎 30 个 1 秒桶都稳定在 20,无掉帧段。
  场景为设置列表连续上下滚动(adb swipe 循环),非 JPEG 回退帧凑数。
- 点击测量:设置首页 WLAN 行(归一化 0.5,0.46);每次 pointerdown 记录
  起点,首个灰度均差 >1.5 的绘制帧为回显帧,随后 adb BACK 复位;
  30/30 次都产生可见变化(diff 2.5–5.3)。Chrome 首个样本 511.7ms 是
  首次进入 WLAN 页(activity 冷启动)的离群值。

### 已执行的其他场景

- **首次出流**:视频 WS 先挂通道(出生订阅)时 ready≈0.9s、首个关键帧
  ≈0.9s,页面加载后约 1.4s 切到 Canvas(486×1080),在 2s 预算内。
- **旋转**:设备浏览器横屏(1080×486)→ 竖屏(486×1080),canvas 与
  stage 跟随重设,帧持续,未回退 JPEG。
- **熄屏/亮屏**:熄屏后编码器停止出帧,画面停在末帧且无错误提示;
  亮屏并滚动后帧恢复。
- **杀 server 恢复**:设备端 `pkill -f 'com.genymobile.scrcpy.Server'`
  后视频 WS 报"视频通道不健康",前端回退 JPEG 档(截图轮询持续出帧,
  提示"H.264 直解中断,回退 JPEG 档"),画面不黑屏。

### 本轮复跑的自动验证(新鲜证据)

- 单元全量:`pytest tests/unit -q` → exit 0,623 项(622 passed,
  1 skipped,device 标记无真机跳过)。
- 设备:`pytest tests/device/test_viewer_stream_device.py
  tests/device/test_control_injection_device.py --mobile-config
  tests/device/local.toml` → **6 passed**(含动态画面 fps、杀 server
  回退与恢复、手势 WS 全链路拖拽)。
- `ruff check src tests` → All checks passed;`node --test
  tests/unit/video_player.test.js` → 1 passed。

### 验收中发现并修复的缺陷

1. **预热死循环**:`_build_stream_frame` 把"视频流尚无解码帧"当作通道
   损坏 → invalidate → 重建,每次预览轮询都重开 server。修复:
   `UpstreamChannel.has_frame` + 预热期不回收;回归
   `test_warmup_channel_not_recycled_and_recovers`。
2. **浏览器内控制全线失效**:`app.js` 从未定义 `normCoords`,
   pointerdown 直接 ReferenceError。已补。
3. **H.264 档首帧体验**:原实现立即关 JPEG 并显示空 canvas。改为 JPEG
   优先 + 服务端 `ready` 起算 2s 预算 + 首个 H.264 帧**实际绘制**后
   才切 canvas;滞后角标只在 JPEG 档显示。
4. **设备用例适配上游后端**:fps 用例改用滚动制造动态画面并按内容去重
   计数;杀 server 用例按 `scid=` 匹配上游 cmdline。

### 未解决/需决策

1. **晚到视频订阅缺关键帧:已按用户授权修复**,见下节。此前观测为
   出生订阅收到首个 IDR,之后 13 秒动态画面未见第二个 IDR;该窗口
   不能证明编码器永远不发关键帧,但足以否定接入时等待周期 IDR 的
   2s 预算保证。现在新视频订阅重建通道,先挂订阅再启动。
2. **连接超限时计数失配:已修复**,见下节。JPEG/H.264 两条路由均在
   注册前拒绝超限请求,只对已注册连接执行释放。
3. **静态资源缓存策略:已修复**,见下节。页面、JS、CSS 使用
   `Cache-Control: no-cache`,保留 ETag 与 304,每次使用缓存前重新验证。
   已打开的旧页面仍需重新加载,服务端不能主动替换其正在执行的脚本。
4. **规格"连续 3 秒未收到新包算滞后,暂停控制"与上游变化驱动编码
   冲突**:静屏时本就不出包,按字面实现会误判滞后。本轮未实现该规则,
   新鲜度按"最近一次成功取帧"判断。

### 仍未执行

- Firefox:本机未安装,兼容/降级矩阵未验证。
- 双设备:仅一台在线(749622c9)。
- 慢客户端(限速/弱网)未测;双浏览器控制互斥的浏览器侧完整验证未做。
  关键帧修复后已验证同浏览器第二页面能进入 H.264,原页面回退 JPEG;
  控制互斥仍由单测覆盖。

### 关键帧重订阅修复复验(2026-10-29)

- 用户选定方案:没有可回放完整解码链时重建通道。当前中继只缓存
  config/session,因此 `get_for_video()` 每次在同一启动锁内停旧通道,
  先订阅再启动,捕获新的启动 IDR;无需新增依赖或缓存 GOP。
- `UpstreamChannel.stop()` 同时关闭旧编码订阅,唤醒阻塞消费者。
  同设备已有视频客户端会短暂中断并回退 JPEG;这不是多页面同时
  保持 H.264 的实现。规格和工单 02 已同步该取舍。
- 红→绿:修复前三个重订阅用例明确失败(缺启动关键帧/复用旧通道),
  停通道关闭订阅用例亦明确失败;修复后通过。另补真实 WS 重连中继。
- 单元全量:627 passed,1 skipped(补 WS 重连用例前);
  最终定向回归 **40 passed**,覆盖视频 WS、编码订阅、监督器和 JPEG 流。
- 真机 `test_viewer_stream_device.py --mobile-config tests/device/local.toml`:
  **3 passed**;含 JPEG 先起流后连续两次视频订阅均在通道启动返回
  (对应 ready)后 2s 内收到有效关键帧,原有动态帧率及杀 server 恢复。
- 独立验收浏览器:首次打开、刷新、第二页面均实际绘制
  H.264 Canvas(486×1080);第二页面重建后原页面显示 JPEG,
  `naturalWidth > 0`,并提示视频通道不健康。未扩大声称跨浏览器矩阵。
- ruff:本轮修改的实现与测试文件检查通过。全量单测有既有
  Starlette 弃用和错误协议头用例线程异常警告,退出码为 0。
- 本轮未复测 30s fps/30 次点击 p95,上面的性能数字属于修复前验收;
  本轮验证范围为订阅、重建、关键帧与浏览器恢复。

### 连接计数与静态资源缓存修复(2026-10-29)

- 根因:JPEG/H.264 两条预览路由在注册前判断连接上限,但超限早退
  仍进入无条件释放的 finally;四个活跃连接收到第五个请求后计数
  从 4 错减为 3。修复将拒绝放到注册/清理事务之前,成功注册后才
  成对增减设备计数及应用计数;检查与注册之间无 await。
- 静态挂载使用 `_ViewerStaticFiles`,对静态响应设置
  `Cache-Control: no-cache`;覆盖首页、index.html、JS、CSS 的 GET、
  HEAD 和 304。继续使用框架 ETag/Last-Modified 及条件请求,
  文件修改后旧 ETag 请求返回 200 和新内容,无需每次重复传输未变文件。
- 回归红→绿已执行:两个路由的超限计数用例修复前均复现 3≠4;
  六个缓存用例修复前均因缺缓存头失败。修复后视频 WS 与 HTTP API
  两文件 **38 passed**;同时覆盖连续超限拒绝、混合四连接、正常
  断开后复用名额和 bad-hello 仅释放自身名额。
- 同类排查:全部两个预览计数入口均已修复,唯一静态挂载已覆盖;
  手势 WS 使用单独的 gesture_slots,不走预览连接计数。
- 全量单元 **637 passed,1 skipped,2 warnings**,退出码 0;警告为
  既有 Starlette 弃用和错误协议头用例线程异常。ruff 与差异格式
  检查通过。采用 TestClient 验证 HTTP/WS 实际响应,
  未运行浏览器升级流程或真机性能复测。


### 画面新鲜度与只读门控(2026-10-29)

- 规格偏差(实测证据):spec 原写"连续 3 秒未收到新编码包算滞后,
  暂停控制"。真机实测该编码器**静屏时完全不出包**——连续滚动 13 秒
  共 183 帧,静置期间 0 帧;出生订阅 905ms 收到 flags=1 首帧。按包
  到达计时会把静屏误判为滞后,操作者再也无法点按解冻,规则不可按
  字面实施。已把 spec 该句改为可执行契约并注明修正来源。
- 实施:新增 `_require_live_picture(manager, channel)`,判定"通道存活
  + 预览不是 u2 回退"。`_build_stream_frame` 每次出帧记录来源,
  `_ChannelManager.preview_source()` 返回 (来源, 单调时间);最近一次
  预览帧仍是 `u2-fallback` 且距今 ≤3 秒时,`touch`/`swipe`/`key` 与
  手势 `start`/`move` 返回 503 只读,scrcpy 画面恢复后自动解除。
  `wake` 与手势 `up` 豁免:前者是恢复动作,后者必须发出,否则设备
  停在按住状态。没有预览客户端时没有可被误导的画面,不额外限制。
- 设计取舍:先按"已有解码帧"实现,真机设备用例 4 例立即 503 失败
  (注入本就先于任何预览启动通道),说明该判据把冷启动的合法注入也
  挡掉;改为按"操作者看到的画面来源"判定,既覆盖 spec 的 u2 只读
  要求,又不动冷启动路径。
- 回归红→绿:三个 u2 只读用例(HTTP 三接口、手势 start、手势中途
  move)在停用判据时全部失败(注入成功 / 收到 `ack`),恢复后通过。
  另有静屏用例(3.1 秒无包仍可点按,防止后续误加包到达计时)、
  u2 窗口到期恢复用例、`wake` 豁免用例,均为守护性用例,非红→绿。
- 覆盖范围:控制注入 21 例 + 手势 WS 9 例 **29 passed**;真机
  `test_control_injection_device.py` + `test_viewer_stream_device.py`
  **7 passed**(确认冷启动注入未被门控破坏);全量单元两次整跑为
  **643 passed,1 skipped** 与 **642 passed,1 failed**(失败项是已记录的
  偶发端口残留 `test_debug_viewer::test_start_serves_status_then_stop_closes_port`,
  单跑 6 passed,与本次改动无关)。ruff 与 `git diff --check` 通过。
- 残余风险:视频 socket 静默停摆但控制 socket 仍存活时,健康检查
  判不出(包到达计时本可覆盖)。change-driven 编码器下要彻底消除需
  设备端心跳,超出"不替换 Android 服务"的范围,故记录不实施。

### 关键帧修复后的性能复测(2026-10-29)

同一设备与配置(`max_fps=20`),场景为设置列表 adb 连续上下滚动 40s,
30s 测量窗口完全落在滚动期内(先起滚动再开窗口);点击为设置首页
WLAN 行(归一化 0.5,0.46),每次 pointerdown 记起点、首个灰度均差
>1.5 的绘制帧为回显帧,随后 adb BACK 复位。

| 项 | Chrome 156 | Edge 156.0.4314.50 | 门槛 |
|---|---|---|---|
| 30s 绘制 fps(中位/均值/min/max) | 20 / 20 / 19 / 21 | 20 / 20 / 20 / 20 | ≥18 |
| 30 次可见变化点击 p95 | 477.3ms | 447.9ms | ≤500 |
| 点击回显 中位 / min / max | 415.7 / 384.9 / 483.8ms | 402.8 / 365.4 / 468.5ms | — |

- 两引擎 30 个 1 秒桶全部 ≥19,无掉帧段;fps 上限即服务端 `max_fps=20`。
- 30/30 次点击都产生可见变化(diff 2.4–6.5),无一次靠静态帧凑数;
  两次实测最大值均 <500ms,未出现首轮 Chrome 那种 511.7ms 冷启动离群值。
- 首轮测量曾把窗口开在滚动启动前,出现 0/8/3/7 的前置空桶(中位仍 20);
  该轮已作废重测,上表为滚动期内重新取的干净窗口。

### 双浏览器控制互斥(2026-10-29,浏览器实测)

同一服务端、同一设备,页面 A(Chrome 标签)领用控制权后:

- 同浏览器第二标签与**另一个独立 Chrome 进程**分别尝试启用控制,均被拒:
  `切换失败 control-busy:其他操作者持有该设备的控制权`,勾选自动回退,
  `#stage` 未进入 control-mode。
- 无凭据页面直接 `POST /api/v1/input/touch` 返回 400
  `control-credential-required`,未发生注入。
- 页面 A 在两次抢占尝试后仍持有控制:1s 心跳未被拒、提示保持"控制中";
  实测 `viewer.wake` 与一次手势(down/up @243,540)在动作流水中
  `outcome: ok`,证明凭据仍可用。
- 页面 A 离开(导航到 about:blank)后 7s 内,另一浏览器页面即可领用
  控制并进入 control-mode,验证失联撤权与可重新领用。

### 仍未执行(环境不具备,如实记录)

- **Firefox**:本机未安装,兼容/降级矩阵未执行。现有回退逻辑为
  能力探测失败即保持 JPEG,未取得 Firefox 实测证据。
- **双设备**:仅 `749622c9` 在线,跨设备并发与操作者隔离未实测。
- **慢客户端/弱网**:未构造 1MiB/8 包积压与发送超时;该路径仅有单测
  (溢出→状态+关闭)覆盖,无真实弱网证据。
