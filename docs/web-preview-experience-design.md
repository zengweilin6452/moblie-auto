# 网页投屏与操作流畅度开发设计

> 状态：待评审设计；调研日期：2026-10-27。本文是实施依据，不代表功能已实现或性能已验证。
> 范围：`mobile-auto serve` / `pytest --debug-viewer` 提供的本机回环网页查看器，Windows 主机、Android 真机、现有 Python/FastAPI 与原生 JavaScript 技术栈。

## 1. 目标、现状与验收口径

### 目标

使网页查看器在动态画面下尽量跟随设备，同时让点击、触摸拖动和异常恢复具备可预期的反馈；冻结快照、模板制作、取证和 pytest 执行语义保持原样。沿用现有设备会话、设备租约与动作流水。

当前上游 scrcpy v5.0.1 通道已把 H.264 解码为 JPEG；网页预览每 100 ms 读取一次 JPEG，base64 包装在 JSON WebSocket 中，再以 `img.src=data:` 显示。`UpstreamChannel` 默认 `max_size=1080`、`video_bit_rate=4_000_000`、`max_fps=20`，但网页实际目标约 10fps。每设备 WebSocket 上限 4 个，控制走独立 HTTP 接口。已有真机记录只证明旧预览在一次 10 秒采样中 ≥8fps、服务端故障后 ≤3 秒回退，**没有**覆盖浏览器端解码、端到端延迟或多浏览器表现。见 [现有链路](../src/mobile_auto/stream/upstream.py)、[预览接口](../src/mobile_auto/viewer/app.py)、[现有前端](../src/mobile_auto/viewer/static/app.js)、[验证记录](validation.md)。

| 指标 | 新版验收目标 | 采样与限制 |
| --- | --- | --- |
| 动态画面可见刷新率 | USB、1080 短边以内、单设备单网页，Chrome/Edge 目标 ≥18fps，中位数；不达标但可稳定显示时允许 JPEG 回退 | 连续滚动/动画 30 秒，按**实际绘制帧**统计；静态画面不以 fps 衡量，设备编码上限仍为 20fps |
| 点击到画面变化 | p95 ≤500ms，改进目标 ≤350ms | 30 次有确定画面变化的点击，输入事件起到变化帧首次绘制；分开记控制排队、设备回显和绘制耗时；超时算失败 |
| 帧积压/新鲜度 | 无缓冲增长；可用动态画面连续 3 秒无更新即显示滞后 | 观测每客户端待发送字节、解码队列和最后**新包**时间，不能靠重复发送同一 JPEG 更新“新鲜度” |
| 可靠性 | 不支持 WebCodecs/解码错误时自动回到 JPEG；scrcpy 故障仍按现有 u2 回退，状态明示 | Chrome、Edge、Firefox 各跑功能矩阵；WebCodecs 支持以实际 `isConfigSupported` 为准，不预设浏览器可用 |
| 控制正确性 | 单击、拖动、旋转、失焦和测试占用时无错设备注入、无卡住的按下态 | 真实设备行为断言、服务端动作流水及页面状态共同核验 |

以上是用户确认的**交付门槛**，须在目标硬件与浏览器上测量。旧记录并无足够数据证明这些目标现在可达；阶段 A 的 H.264 投屏不达标就暂停交付，保留原有 JPEG 为默认路径，记录完整分布与环境后定位瓶颈。JPEG 回退可保证可用性，不计入 H.264 刷新率达标样本。

### 边界

包含：浏览器直解上游 H.264 的自适应预览；JPEG/u2 降级；可用状态与延迟观测；统一鼠标/触屏坐标；连续拖动输入的受控实现。保留原始冻结快照与 UI 树语义。排除：远程公网/跨机器开放、音频、录屏、iOS、内网绑定层变更、H.265/AV1、多用户协作与替换设备驱动。多设备按现有注册表独立工作，首先验收单设备，再做双设备资源检查。

## 2. 公开方案研究（2026-10-27 核验）

先查看官方 [scrcpy v5.0.1 服务端](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1) 和[其开发文档](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/develop.md)：视频包含配置、关键帧、时间戳和旋转后重发的会话尺寸；客户端应尽快显示、不堆帧。协议属于**内部协议**，服务端版本必须匹配。官方服务端 Apache-2.0，项目当前已固定该版本，浏览器输出不是 scrcpy 原生功能。

| 项目/一手实现 | 许可及维护状态 | 可借鉴机制 | 集成判断 |
| --- | --- | --- | --- |
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | Apache-2.0；2026-10-26 最近推送；当前项目已用 v5.0.1 | H.264 配置/关键帧标志、旋转会话包、独立控制通道、尽快展示 | 固定服务端版本并沿用现有解析器；不另开 Android 服务 |
| [yume-chan/ya-webadb](https://github.com/yume-chan/ya-webadb)（[H.264 转换](https://github.com/yume-chan/ya-webadb/blob/main/libraries/scrcpy-decoder-webcodecs/src/video/codec/h26x.ts)、[解码队列](https://github.com/yume-chan/ya-webadb/blob/main/libraries/scrcpy-decoder-webcodecs/src/video/utils/video-decoder-stream.ts)、[渲染背压](https://github.com/yume-chan/ya-webadb/blob/main/libraries/scrcpy-decoder-webcodecs/src/video/render/flow-control.ts)） | 仓库与 WebCodecs 模块均 MIT；2026-10-27 最近推送 | Annex B 配置拼接首关键帧、解码等待关键帧、旧渲染帧释放与跳帧 | 借鉴状态机；完整 SDK 以 WebUSB/TS/多版本 scrcpy 为中心，接入本项目会额外引入构建/运行面 |
| [NetrisTV/ws-scrcpy](https://github.com/NetrisTV/ws-scrcpy)（[WebCodecs 播放器](https://github.com/NetrisTV/ws-scrcpy/blob/master/src/app/player/WebCodecsPlayer.ts)、[流接收](https://github.com/NetrisTV/ws-scrcpy/blob/master/src/app/client/StreamReceiver.ts)） | MIT；2026-10-20 最近推送 | WS 二进制视频、浏览器绘制并释放 `VideoFrame`、控制与视频协同 | 不整体迁移 Node 服务与页面；其 `isSupported()` 只检测 API 存在，仍需自行检测 codec 支持 |
| [OpenDroid Remote](https://github.com/hihumanzone/opendroid_remote) | MIT；2026-10-22 最近推送；规模和兼容证据有限 | 浏览器直连 WebUSB 的备选架构 | 需要每位用户配置浏览器 USB 访问，绕开现有 Python 设备会话与设备租约；本项目不采用 |

维护时间和许可证来自上述 GitHub 仓库元数据及 LICENSE 的只读核对。**不复制第三方源码**；如后续引入其包或代码，应固定版本、保存许可声明并核查传递依赖。浏览器能力以 [MDN `VideoDecoder`](https://developer.mozilla.org/en-US/docs/Web/API/VideoDecoder) / [`isConfigSupported`](https://developer.mozilla.org/en-US/docs/Web/API/VideoDecoder/isConfigSupported_static) 和实际浏览器测试共同判定：需安全上下文；回环 `localhost` 为当前目标，远程 HTTP 不在支持范围。

### 选择

**推荐**：复用 Python 中已解析的 scrcpy v5.0.1 码流；提供二进制 WebSocket 视频档，浏览器 `VideoDecoder` 解码、Canvas 展示；原有 JPEG WebSocket 保持原样用于不支持/失败回退，原有 u2 故障回退保持原样。控制继续经 Python 设备租约。这样不引入 Node 服务、npm 构建和新用户设置。

最小方案是仅把 JPEG 发送间隔从 100ms 调到 50ms，再换用二进制 JPEG 和 `createImageBitmap`；改动小，但仍每帧执行主机 H.264 解码、JPEG 编码及浏览器 JPEG 解码，无法消除核心处理路径。WebRTC 需要额外的重封装/信令/媒体服务，局域回环场景无足够收益；MSE 分片和缓冲更难保持低延迟。可在关键帧重同步或浏览器兼容矩阵不达标时，退回 JPEG 优化方案。

**最脆弱前提**：现有 `UpstreamChannel` 的解包能同时供给 PyAV 与浏览器，不增加设备端第二个编码器，且目标浏览器支持该设备实际输出的 H.264 profile。若做不到，浏览器始终可切回 JPEG；不能绕开当前取证/图像匹配去关闭 Python 解码。

## 3. 架构与状态

```text
Android scrcpy v5.0.1 (video/control 两 socket)
             │
             ▼
UpstreamChannel 单次读包/解析 (Python，同一设备唯一生产者)
             ├── PyAV → 最新 JPEG → 取证/图像路径 + 旧预览 WS → img
             └── H.264 配置/会话/帧包 → 每设备广播 → 二进制 WS → VideoDecoder → canvas
                         ▲                                    │
                         └── 连接模式/状态/反馈 ◀───────────────┘
设备租约 → 设备队列 → scrcpy 控制 socket（既有 HTTP 控制 / 新连续手势）
```

数据方向清晰：WebCodecs 浏览器只接收视频，控制命令独立进入租约队列；UI 树及冻结快照不消费浏览器帧。每设备一次读取和解包，多个观察者共享编码帧，但每客户端独立背压、独立重新同步。编码帧是压缩格式，只允许在完整访问单元边界分发，不能把 TCP `recv` 块误当视频帧。

### 服务端视频契约

- `UpstreamChannel` 在现有 `_video_pump` 解包处保留**本代最新配置包和会话尺寸**，同时继续喂原 PyAV 解码器。新增只读的“订阅编码包”接口，退出时关闭订阅；仅 upstream 后端提供，wetest 直接进入既有 JPEG 档。新订阅者等待下一关键帧，等待期间保持旧 JPEG 画面和解码中的提示；不缓存 10 秒依赖链冒充零等待。若设备通道为首次启动，新视频订阅需先挂载、再启动解包，避免错过首个关键帧；已有通道的新订阅者受下一关键帧等待时间约束。泵线程向有界队列投递不得阻塞 PyAV 和取证路径。
- 新增 `GET /api/v1/preview/capabilities?device=`，回复 `h264_ws`（当前后端支持且通道可尝试）、`jpeg_ws`、固定 `server_protocol` 版本与当前 `stream_backend`，不主动启动设备服务。原 `/api/v1/preview?device=` 的 JSON/JPEG 契约**不改**。
- 新增 `/api/v1/preview/video?device=` WebSocket：先校验 Host 和 Origin（同 HTTP 回环规则，包含端口/协议精确同源校验）及每设备已有的 4 客户端总上限，复用 `_ChannelManager` 用户计数、启动租约、退避与 30 秒空闲停止。成功后发送一次 JSON `hello`：协议版本 `1`、设备标识、codec `h264`、stream generation；不支持/设备离线用可识别状态消息并关闭。后续状态用 JSON，媒体用二进制。`generation` 是本设备通道重建代号，新 WS 在同一代加入时沿用该代号；客户端另外用本地连接代号隔离重连消息。旧 `/preview` 的 Origin 验证保留，新视频 WS 和控制 WS 必须额外校验 Host、Origin 的 scheme/host/port 与当前服务端监听源完全一致，缺失 Origin 拒绝。
- 二进制消息固定网络字节序：`version u8=1 | kind u8 (1=config, 2=session, 3=frame) | generation u32 | sequence u32 | pts_us u64 | flags u8 (bit0=key) | width u16 | height u16 | payload_length u32 | payload`。包头合计 **27 字节**（1+1+4+4+8+1+2+2+4）；`config` 携带 Annex B SPS/PPS；`session` 携带本次视频宽高；`frame` 携带完整编码访问单元，时间戳使用 scrcpy PTS（**不是墙钟**）；config/session 的 PTS 为 0。严格校验长度/尺寸/单包最大 2MiB 和协议版本，异常客户端/设备包关闭当前流并报告状态；**通道重建**才递增 generation，每个新 WS 重新从 0 递增 sequence。封包测试覆盖大端、边界与畸形载荷。
- 新订阅者先获得最近配置与会话尺寸，再等**下一关键帧**；发送非关键帧前必须已送该代关键帧。现有 JPEG 预览继续显示到 H.264 首帧绘制完成后才关闭其连接，过渡期同一浏览器占两个客户端槽位，合并计入每设备 4 个连接上限；若无空槽则继续 JPEG 并提示，不抢占其它用户。scrcpy v5.0.1 当前服务端默认关键帧周期 10 秒；超出 2 秒仍无首个可解码帧时保持 JPEG，结束本次 H.264 尝试；后续仅在重连/切换设备时重试，避免循环建连。不要以未知语法的 codec option 缩短间隔当作交付前提。确需缩短间隔时单独验证 [上游 SurfaceEncoder](https://github.com/Genymobile/scrcpy/blob/v5.0.1/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java) 的设备兼容性、码率与取证影响；实施时仍按实测结果决定是否启用。
- 每客户端队列限制 1MiB 或 8 个完整包（先到者生效），发送超时沿用 5 秒；达到上限或浏览器解码队列连续 1 秒超过 4 帧时**终止该视频 WS，保留或恢复 JPEG**，不能丢一个 P 帧后继续送旧依赖链。后续重连以新 generation 从配置/下一关键帧开始；避免 WebSocket/TCP 队列变成隐藏延迟。**仅渲染输出帧可以无条件丢旧保新**，送入解码器的依赖链必须完整。
- 同一端口、仅回环监听；HTTP 与 WS 都校验 Origin 和 Host，拒绝不受信的跨站操控。服务端保留按设备/订阅者限额。状态、日志、动作流水只记录不含画面的计数/耗时，截图素材不外传。

### 浏览器画面状态机与坐标

`探测 → 浏览器可解码且 upstream → 待配置/关键帧 → 视频播放`；否则 `JPEG 播放`；后端 scrcpy 失败则 `u2 截图/暂停`。每次切换设备、旋转、代际变化、重新连线或回到实时视图，都丢旧编码包、等待新配置与关键帧；不能用旧设备的帧或画面尺寸填新设备画面。WebCodecs 的 CONFIG（Annex B SPS/PPS）与首个 IDR 合并、以 profile 生成准确的 `avc1` codec 标识；先 `isConfigSupported` 再 `configure`；首帧 type=`key`，后续由包头标记；只在完整帧上调用 `decode`。`VideoFrame` 绘制后 `close()`，异常/切换时 `decoder.close()`；输出有新帧才排 `requestAnimationFrame`，渲染只保留最新已解码帧。

`#stage` 的显示盒必须严格等于 `<canvas>` 或回退 `<img>` 的**有效内容尺寸**；旋转和窗口 resize 重新计算等比尺寸，Canvas 与 img 共享当前一份尺寸映射，不把 letterbox 区当有效点击区域。冻结检查仍展示服务端快照 PNG，不让浏览器 H.264 画面参与模板像素匹配。独立统计新视频包、解码完成帧、实际绘制帧；静止画面不反复把同一 JPEG 标记为新帧，scrcpy 编码器主动重复出的包可视为新包。服务端以视频泵新包到达时间判断控制画面新鲜度，连续 3 秒无包视为滞后；断流/失焦/不可解码时显式显示暂停或回退、触摸禁用直至新画面坐标有效。

### 控制体验

- 第一阶段统一 `PointerEvent` 处理鼠标/触摸/笔：`pointerdown` 仅在“实时 + 本页面持有控制权 + 新鲜 scrcpy 视频/JPEG 画面 + 视频尺寸有效”进入，`setPointerCapture()`，`pointerup` 判断单击或滑动；`pointercancel`/设备切换/断流/u2 回退/退出实时直接取消。归一化坐标继续在服务端与当前坐标基准比对后换算，旋转时重新校准。JPEG 来自健康 scrcpy 且新鲜时仍可控制，u2 或滞后画面必须禁用控制。等待结果时标明“发送中/成功/拒绝”，409 租约冲突不能伪装成成功。阶段 A 保持原控制接口，阶段 B 再升级为单操作者契约。
- 第二阶段为每设备建立一个**操作者控制权**：`POST /api/v1/control?device=` 显式启用时，只为该页面生成不写日志、仅留在页面内存的临时控制凭据；其它页面只读、不可抢占。`/api/v1/input/touch|swipe|key|wake` 均必须用 `X-Viewer-Control` 请求头携带本设备凭据，无凭据的旧 HTTP 客户端明确拒绝并按迁移说明升级；禁用控制需持有者凭据。新增 `POST /api/v1/control/heartbeat?device=` 持证心跳（每秒一次），浏览器卸载时尽力发送持证 release；丢失心跳后 5 秒内撤销控制权，不允许新请求。只有已有操作者凭据可连接 `/api/v1/input/gesture?device=` 专用同源 WS；凭据在握手后首条 JSON 消息中发送，不接受 URL 传凭据，认证前不可注入，每台设备同一时间只允许一个主动手势。页面关闭/心跳失联需先完成在途设备调用及尽力抬指，再交接租约；若调用无法在 5 秒内完成，按 5 秒撤销网页控制权但仍保持设备租约，直到发送结局可判定。原全局 `control_enabled`/`last_inject` 改成按设备、按操作者管理，50ms 节流仍只针对离散输入。
- `start`（单指 + 指针 ID + 首点）取得设备租约，经过同一设备队列发 DOWN；`move` 在客户端节流至 20Hz 并合并未发送位置，在服务端队列中按序发 MOVE；`end` 必须发 UP 后释放租约。每一输入消息含 `gesture_id/sequence/nx/ny`，服务端验证单调递增、同设备及当前视频尺寸/坐标基准；成功发送每一步后才答 `accepted`（只是“已发往通道”，并不代表业务效果）。控制模式开关关闭、断线、失焦或服务端 2 秒无手势消息时**在队列中尽力发送 UP**，完成后释放租约；若设备通道断开则报告结果未知并在设备调用结束后回收租约，不能承诺撤销已发送的 DOWN。只允许一根手指；按现有动作流水记录控制序列和取消结局。握有手势租约时其它写操作按现有规则 409 拒绝，不绕过 pytest。握锁超时/队列卡住时沿用 `_LeaseGuard`“发送结束再释放”规则。
- 仅在收到服务端明确确认**未进入发送阶段**、且本手势尚无设备侧 DOWN 时，允许退到旧 `/input/swipe` 的“抬指后整段发送”；一旦 DOWN 已送或发送结局未知，绝不自动补发滑动，提示用户查看设备状态后自行重试。旧路径依然可用。这里的重点是拖动时**真实设备同步移动**，不是把多条 `/input/swipe` 拼接成反复 DOWN/UP。

## 4. 实施顺序与接口文件

每阶段独立发布后都有完整的可用投屏与控制，不出现仅部分阶段才能运行的断层。现有工具栈内完成，无新增服务、安装步骤、环境变量或 npm 依赖；新增的是内部 HTTP/WS 接口与浏览器脚本文件。整体预计涉及 **超过 8 个文件**（核心 4～6 个 + 测试/文档 4～6 个），后端视频分发与手势分别评审。

| 阶段 | 内容、主要文件与独立交付 | 验收 |
| --- | --- | --- |
| A：视频直解 | `src/mobile_auto/stream/upstream.py` 增加编码包订阅；`src/mobile_auto/viewer/app.py` 加能力探测/二进制 WS 与丢包状态；`src/mobile_auto/viewer/static/app.js` 拆出少量 `video_player.js` 负责 WebCodecs 状态；`index.html`、`styles.css` 增 Canvas 与回退提示；`tests/unit/test_upstream_channel.py`、`tests/unit/test_viewer_stream.py` 新增协议/订阅测试，浏览器与真机验收补到相应测试/`docs/validation.md`。交付后原控制与 JPEG/u2 路径完整可用。 | Chrome/Edge 功能及指标，Firefox 不支持时自动 JPEG，双浏览器观察同设备与旋转、杀 server、设备拔线/恢复 |
| B：连续操作 | `app.js` 用 PointerEvent 统一手势生命周期；`app.py` 新增按设备控制权、凭据与受租约门控的 gesture WS；`stream/upstream.py` 复用 touch 原语；`tests/unit/test_control_injection.py`、`tests/device/test_control_injection_device.py` 添加取消/占用/回显；更新 `README.md` 中旧 HTTP 客户端持证用法和 `docs/validation.md`。发布后旧点击/滑动 API 路径保留，但请求必须持证。 | 单操作者与失联 5 秒内撤权、点按、慢拖、快拖、触屏、跨边界/转向、失焦/断线释放、pytest 持租约 409，动作流水可对应实际发送 |

阶段 A 调整现有视频内部协议，要确保 PyAV `latest_frame()`、`capture_png()`、图像匹配和失败证据完整回归；阶段 B 不改变 15 方法参考契约。正式实施前按照项目约定建立 `.scratch/web-preview-experience/spec.md` 和逐项任务（链接本文而非复制全文），并核查当时的仓库差异。冻结快照/拾取、缓存和控制模块可按文件独立开发，但两个阶段碰到的 `app.py`/`app.js` 顺序实施，避免并发覆盖。

## 5. 验证、回退与风险

### 定向自动验证（实施后执行）

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_upstream_proto.py tests/unit/test_upstream_channel.py tests/unit/test_viewer_stream.py tests/unit/test_control_injection.py -q
```

```bash
.venv/Scripts/python.exe -m pytest tests/device/test_viewer_stream_device.py tests/device/test_control_injection_device.py -m device -q
```

真机命令依现有 `tests/device/conftest.py` 所需的设备配置运行；没有已授权设备时记“未执行”，不能记通过。前端用真实 `mobile-auto serve`（或 debug-viewer）在 Chrome/Edge/Firefox 浏览器逐项验证，记录浏览器版本、设备/Android 版本、USB 或网络连接、窗口尺寸与 30 秒视频输出数据：首屏时间、实际绘制 fps、p50/p95 点击回显、解码队列峰值、内存/CPU、丢帧/回退次数。动态与静态画面各验收一次；控制用真实 UI 状态变化核对，不能只凭 WS 消息或控制台无报错判定。

重点错误/边界矩阵：无 WebCodecs、codec profile 不支持、配置包与关键帧晚到、慢客户端、JPEG 解码失败、前后台切换、分辨率变化/旋转、server 杀死、USB 拔线、4 客户端限额、未登录设备、控制关闭、测试持租约、双浏览器抢控制、手势 DOWN 后 WebSocket 断开。回归取证与匹配精度，因为服务端解码尚在并行执行。

**依赖失败**：浏览器解码不可用回 JPEG；设备 server 不可用回 u2/明确暂停；PyAV 不可用按已有流能力错误处理，不把缺依赖解释成 WebCodecs 成功。**规模**：4 客户端单设备，每客户端积压有界；两台设备同时推流时量测 CPU/带宽，资源超标先降至 JPEG/只读提示，不默默攀升。**回滚**：阶段 A 服务端新 WS 旁路新增，旧 JSON WS 与驱动接口始终保留；阶段 B 新 gesture WS 旁路新增但旧触摸/滑动接口必须携带新控制凭据，旧 HTTP 调用方要按 README 升级；回滚旧版本会恢复原全局控制语义，不能称作保持新单操作者能力。无 schema/数据迁移。服务端资源回收继续使用现有监督器。

### 安全与许可

保持仅回环监听与同源检查。尤其新 WebSocket 不能只验 `Origin` 的主机名而忽略端口、协议或 `Host`；WS 连接没有 Origin 时按浏览器端点策略拒绝，防止别的本地页面跨站操控。用户直接控制仍显式开启；测试运行期间只读视频可继续，写操作受设备租约与设备队列约束。无需新的 API Key、Token 或第三方账号。若未来选择引入 Tango/npm 包，需重新评估额外构建依赖和其 MIT 许可传递要求。

### 待实测的决定性条件

- 在目标 Windows 浏览器和 Android 编码器上确认 H.264 profile 可解码、Annex B 配置拼接正确；缩短关键帧间隔仅是可选优化，启用前需实测码率与取证影响。负责人：实施者；直解不成立或性能未过验收门槛，阶段 A 暂停交付并保留原 JPEG 为默认路径。
- 旧网页当前不提供“真正变化的帧龄”；新时间口径须基于包 PTS/本地单调时钟，区分服务端到达、浏览器解码与绘制。负责人：实施者；未完成不能声称端到端实时。
- 多浏览器和 Android 版本矩阵的数据目前没有；在目标设备执行前只视为目标，不视为通过。负责人：实施验收者。

本文与旧 P1 spec 的“网页端 H.264 解码不在范围内”[记录](../.scratch/p1-experience-loop/spec.md)存在范围变化：这是新的 P1 后续能力，不回写历史验收结论；沿用 [PRD §8.2](prd-auto.md) 对解码验证与截图回退的要求，并在新需求批准后实施。
