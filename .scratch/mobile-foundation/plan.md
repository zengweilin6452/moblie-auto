# Android 移动能力包：PRD 复审与详细开发方案

- 日期：2026-10-24（含同日第二轮 grilling / domain-modeling 复审修订）
- 状态：设计决定 Q1–Q4 已确认（见 3.1）；待用户批准实施，未开始编码
- 需求依据：`docs/prd-auto.md`、用户提供的 `app自动化.md`（2026-10-23）
- 配置依据：`CLAUDE.md`、`docs/agents/domain.md`、`docs/agents/issue-tracker.md`；术语见根目录 `GLOSSARY.md`
- 审查性质：同一作者上下文内两轮复审（第二轮使用 grilling 与 domain-modeling 技能），未进行独立上下文审查

## 1. 结论与实施边界

推荐交付独立 Python 包 `mobile_auto`、一个本机 FastAPI 网页服务，以及面向内网真实类型的薄绑定层。pytest 直接调用 Python 驱动，不经过 HTTP；网页与驱动共享定位、匹配和会话实现。

最小可行路径是先交付无网页的驱动包：连接一台设备，完成原生和图像定位、操作、等待及证据采集。推荐在此基础上分阶段增加网页能力，原因是每阶段均可独立使用、单独回退，不需要所有模块完成后才验证价值。

不建设：OCR、IMAGE 输入、WebAdapter、内网业务页面、远程设备平台、多设备调度、同用例跨端编排、scrcpy 加速和网页直接控制。这些后两项属于 PRD 的 P1，本开发方案只覆盖 P0。

本方案预计涉及超过 8 个文件，新增 1 个本机 HTTP 服务。网页使用随 Python 包分发的 HTML/CSS 和少量原生浏览器 JavaScript，不引入 Node/npm、前端框架或第二个构建运行时。此技术选择作为本方案批准内容，不在批准前创建源码。

最脆弱的假设是：接入说明反映内网真实 Port 的大体形态。如果不成立，内网绑定层需要调整；通过执行核心不依赖内网类型来限制返工范围。缺失的 ListItemSchema 阻塞最终列表绑定，但不阻塞独立驱动与网页的开发。

## 2. 当前证据与工具检查

### 2.1 仓库现状

当前目录仅包含 PRD、项目 CLAUDE.md 和两个工程技能配置文档。不是 Git 仓库，没有 Python 项目配置、依赖锁、源码或测试；不存在可复用的本地驱动实现。本方案提出的默认值都是新的本地设计值，不是从现有配置读取的值，也不声称是内网默认值。

内网说明展示 15 个异步方法和一个 platform 属性，虽然文字多次称 13 个。按完整清单跟踪，不沿用错误计数。

### 2.2 已执行的只读检查

- 当前 shell 可找到 Python、py、uv、node、git；未在 PATH 找到 adb，这不证明磁盘上不存在 ADB。
- 当前 `python` 为 3.12.10。
- 当前解释器可发现 fastapi、pytest、Pillow、filelock；未发现 uiautomator2、cv2、pytest_asyncio。
- 未安装或升级任何依赖，未连接真机，未启动服务，未修改 PRD。
- WebSearch 可检索官方文档摘要。WebFetch 对 GitHub、Python 和 OpenCV 页面直读受限，未获得成熟项目的完整实现源码；不宣称已完成开源源码对照审查。

### 2.3 官方能力对设计的约束

- uiautomator2 官方文档提供原生 selector、XPath、App 启动及截图能力，优先封装这些能力，不自建 Android instrumentation 驱动。
- 官方源码的搜索片段显示 screenshot 默认返回 Pillow 格式；本包显式编码 PNG bytes，不依赖未经核验的 `format="raw"`。
- OpenCV matchTemplate 提供匹配分数矩阵；NMS、ROI 偏移、稳定排序和点击决策属于本包职责，不能把示例阈值称为概率。
- Python concurrent.futures 明确：已运行的 Future 不能通过 cancel 停止；取消异步等待并不能证明设备操作未发生。
- FastAPI 官方推荐 lifespan 管理初始化和清理，并提供 StaticFiles 与 WebSocket。使用这些内置机制，不自建服务容器。

详细来源见第十五节。精确依赖版本和设备适配必须由安装后测试和发布锁文件证明，搜索结果不能证明当前环境可运行。

## 3. PRD 复审发现与建议裁决

| 编号 | 严重性与问题 | 触发场景和影响 | 本方案裁决 |
| :--- | :--- | :--- | :--- |
| R01 | 阻塞完整验收：ListItemSchema 缺失但 P0 要求全部 Port | 无法实现真实 `extract_list` 的字段、嵌套和缺失值语义 | 独立能力发布可完成；全 Port 内网绑定必须以实际 schema 和行为样例为门槛，不造一个同名公共 schema |
| R02 | 高：取消后“无迟到操作”承诺过强 | 线程或设备已接收输入，调用者取消等待后操作仍可能完成 | 每个操作携带取消标记，进入设备发送阶段前在锁内做最后取消检查：未进入发送阶段的取消不得发送；已进入的只能报告结果未知。不自动重放，在途请求结束前不释放占用；已发送动作结果可为 unknown，不承诺撤销 |
| R03 | 高：会话级连接与用例级占用未完全区分 | 用例结束调用 close 误关共享连接，或 session 持锁导致网页一直不可用 | session 持有设备对象，function 持有操作租约；显式 owned/borrowed 两种 Adapter 所有权 |
| R04 | 高：存在判断和唯一性混用 | 两个同图标存在时 `wait_for` 因歧义失败，影响等待语义 | 无 index 的 bool 查询判断至少一个可见匹配；单目标操作仍要求唯一；具体内网差异只在绑定层转换 |
| R05 | 高：IMAGE 句柄按 index 重匹配可能换目标 | 动态列表插入一个图标，旧 index 指向另一个业务项 | 句柄验证模板摘要、设备/会话、尺寸/方向及位置一致性；候选身份无法确认时 StaleHandle，不重定向 |
| R06 | 高：就绪等待、输入和滚动仍是未定语义 | 执行者需要临场决定何谓页面加载完成、文本替换和下滑 | 第五节给出独立核心的确定语义；真实 Port 不同则绑定时显式转换，不要求内网改 Port |
| R07 | 中：PRD 阶段 A 只有骨架与验证 | 阶段结束没有可被用户使用的交付物 | 调查已在方案前执行，第一开发阶段直接交付可运行原生驱动，不设单独空壳阶段 |
| R08 | 中：离线部署验收条件未锁定 | 本地装包成功，但内网缺 wheel、前端资源或 u2 设备端组件 | 在线运行无依赖与离线安装分开验收；发布清单必须覆盖主机和设备端文件，内网结果单列 |
| R09 | 中：宽泛异常或健康结论 | UI 树失败时仍可匹配截图，却将设备判定完全不可用 | 分开报告连接、截图、树、模板能力；按失败来源报错，禁止静默切换定位策略 |

建议批准方案时同步批准对 PRD 的 R02、R04、R05、R07 定向修订；R01 不伪装成已解决，保留内网门槛。当前 PRD 未改动。

### 3.1 第二轮复审确认的决定（2026-10-24，用户已逐项确认）

| 编号 | 决定 |
| :--- | :--- |
| Q1 | 首版接受挂起故障的影响范围为宿主进程：底层设备调用无法结束时，当前 pytest/调试进程停止后续设备操作并优先保存已有证据，由操作者确认设备与 App 状态后重启进程；不为此引入设备操作子进程 |
| Q2 | `wait_for_load` 仅承担 App 启动就绪（配置项更名为 `launch_ready_target`）；业务页面就绪由页面对象显式 `wait_for(Target)` 表达；内网绑定时核对真实调用点，不同名绑定不静默替换语义 |
| Q3 | 模板匹配必须区分形状相同、颜色不同的状态：灰度相关性只做候选定位，候选区域须再通过颜色差异验证；颜色校验属模板匹配能力，不引入 OCR 或视觉大模型 |
| Q4 | 不开发 Android 测试 App：真机操作用固定来源的现成演示 App 验证，图像算法用固定截图样本验证，取消与竞态用模拟设备验证；三类证据分别记录，互不替代 |

### 3.2 第二轮复审修正清单（已并入本方案对应章节）

- 取消竞态：操作级取消标记与发送阶段检查点（7.1/7.3）；“匹配完成后、点击发送前取消”列为必测路径（12.1）
- 挂起时取证：会话故障后失败钩子不再调用设备采集，只保存已有画面、操作记录与“无法采集”原因（7.3）
- 模板一致性：模板按版本不可变存储，图片与元数据完整写入后经清单原子切换当前版本；更新在资产级锁内做摘要检查（6.3）
- 内存预算覆盖解码缓冲与算法工作区，匹配作业并发受限；编辑期间冻结快照固定引用，不受实时快照上限驱逐（8.3）
- 证据访问闭环：驱动写证据并登记运行清单，网页只读清单登记项（8.2）
- `doctor` 仅执行无副作用检查，主动探测与设备端初始化分离为显式步骤（9.2）
- 绑定层交付落实：绑定骨架、契约测试模板与契约版本记录纳入交付物（10/13）
- 灰度阈值、NMS IoU、句柄 IoU、颜色阈值与坐标容差均标记为“样本校准初值”，非已证明值（6.1/6.2）
- OpenCV 共存：本地隔离环境用 headless 发行包；内网接入不得覆盖既有 OpenCV，依赖组合单独验证（9.3/13）
- 统一术语已写入根目录 `GLOSSARY.md`（设备会话、设备租约、操作、发送阶段、定位描述、查询句柄、匹配结果、模板版本、启动就绪目标、冻结快照、实时预览帧、失败证据、参考契约、绑定层）

## 4. 推荐架构及替代方案

```text
独立 pytest / 内网真实 Port 绑定
             |
             v
       MobileAdapter --------> 原生解析 / 图像匹配 / 证据
             |                            ^
             v                            |
       DeviceSession <--------- 本机 FastAPI 服务 <--- 浏览器
             |
       串行设备操作 + 跨进程租约
             |
        uiautomator2 / ADB
             |
          Android 真机

资产目录 ---> 图像匹配与网页模板管理
```

依赖单向：网页依赖核心，核心不依赖 FastAPI；兼容层依赖核心，核心不导入内网项目；匹配器不调用设备。证据模块接受数据，不反向控制 Adapter。无循环调用链。

不选择“所有调用统一经过常驻设备 HTTP 服务”：它能集中仲裁，但会让现有异步 Port 引入网络序列化、服务启动和部署依赖，削弱独立库接入。只有未来明确要求多用户远程控制时，才有理由重新评估。

不直接 fork 整套 Inspector：当前无法核验其版本和内部扩展边界；采用 u2、OpenCV、FastAPI 的已有能力，保持业务工作流和内网契约在自己的薄层中。拿到一个许可证合适、可离线分发且支持相同模板工作流的成熟实现时，才可能改变网页自建的判断。

## 5. Python 公共接口与本地确定行为

### 5.1 类型与入口

本地导出 `MobileAdapter`、`DeviceSession`、`MobileConfig`、`Target`、`Locator`、`ElementHandle`。这些是包内参考类型，不复制为内网 `core.ui_driver` 的替代定义。

内网绑定负责将真实 Target 的枚举值和字段归一化，并将本地句柄包装进真实 ElementHandle。不按类名猜类型，不假设两个 dataclass 的 isinstance 兼容。PNG bytes、str、bool、None 等返回值直接透传；异常按照批准的内网映射表转换，保留原始 cause。

平台返回 android。Locator 支持 RESOURCE_ID、TEXT、ACCESSIBILITY_ID、U2_SELECTOR、XPATH、IMAGE；CSS 明确不支持。

### 5.2 原生定位规则

- RESOURCE_ID、TEXT、ACCESSIBILITY_ID 使用精确匹配；TEXT 不做隐式包含搜索。
- U2_SELECTOR 本地采用 JSON object 字符串；只允许 `resourceId`、`text`、`description`、`className`、`packageName`、`clickable`、`enabled`、`selected`、`checked`，值类型逐项校验。拒绝代码、正则和未知字段。
- XPath 使用 u2 XPath 能力，不自动重写为 CSS 或其他定位策略。
- index 必须为非负整数；不接受 bool、负数和字符串数字。单目标未指定 index 且多匹配时立即报歧义。
- `find_elements` 无 index 返回全部可见匹配，顺序为当前 UI 树遍历顺序；有 index 返回对应的零个或一个句柄。
- 可见性：有效非空 bounds 与屏幕相交；不承诺检测所有遮挡。操作前重新定位并检查可用性。
- 无 index 的 `is_visible` / `wait_for`：存在至少一个可见匹配即可；有 index 则要求该序号存在。查询错误不能变成 False。

### 5.3 操作行为

| 方法 | 本地执行核心的明确行为 |
| :--- | :--- |
| goto | 空字符串按已配置 package/activity 启动；非空只接受配置中允许的 Deep Link scheme，通过参数化 ADB 调用；未配置或非法链接立即报配置错误，不接受 shell 字符串 |
| wait_for_load | 仅承担 App 启动就绪（Q2）：等待配置的 `launch_ready_target` 可见；缺配置报错，超时抛 WaitTimeout；业务页面就绪由调用方 `wait_for(Target)` 表达，本方法不维护页面级就绪表，不以 sleep 替代 |
| back | 发送一次 Android back，不自动等待页面就绪 |
| click | 等待单目标可见，原生节点使用驱动 click；IMAGE 使用第六节流程；每次只发送一次动作 |
| input_text | 仅原生目标，清空并替换；拒绝不可编辑目标；不通过读回敏感值做日志验证；不隐式追加 |
| get_text | 返回原生文本，缺失文本归一为空字符串；IMAGE 报 UnsupportedOperation |
| get_attribute | 支持 text、resourceId、contentDescription、className、packageName、enabled、clickable、selected、checked、bounds；布尔以 true/false 字符串返回，bounds 用固定四整数表示；未知属性报错，已知但缺失返回空串 |
| wait_for | 默认 10 秒，轮询 0.5 秒；timeout=0 查询一次，负数或非有限值报错；存在返回 True，截止未找到返回 False |
| is_visible | 单次查询，不进行长等待 |
| find_elements | 单次查询，零结果返回空列表；句柄包含重查询条件及快照身份信息 |
| extract_list | 真实 ListItemSchema 未确认前，不对外声称支持；完整内网绑定的发布门槛见第十三节，不新增伪兼容 schema |
| screenshot | 新采集图像显式编码 PNG，返回 bytes；内部记录采集时间与方向，不返回缓存帧 |
| press_key | 支持 back、home、enter、search、delete、recent；未知键报错，不开放任意 keycode/shell |
| scroll | direction 指视口沿内容移动方向，down 对应手指向上；amount 为正整数截图像素，夹限到屏幕安全区域，手势时长 0.3 秒；不承诺实际内容恰好移动同样像素 |
| close | 幂等；borrowed 仅释放自身租约，owned 还关闭会话调度；不停止 App，不重启或杀死外部 ADB |

click/input/get_text/get_attribute 的查找默认上限 10 秒；每轮底层查找不得再叠加一层默认长等待。底层隐式等待设置必须在固定 u2 版本上核验为单次查询。超时与耗时记录使用 monotonic 时钟，证据时间另用 UTC。

以上是本地核心合同，不是对内网默认值的断言。若摘要或真实源码与之冲突，修改 binding，不静默改变已经测试的核心规则。

### 5.4 错误类别

InvalidTarget、ConfigurationError、DeviceUnavailable、DeviceBusy、ElementNotFound、AmbiguousTarget、StaleHandle、UnsupportedOperation、WaitTimeout、InvalidTemplate、MatchOverflow、OperationOutcomeUnknown、SessionFaulted。

每类对应具体调用失败，没有 catch-all 后成功返回。Python CancelledError 正常传播，但任务结果单独记录 sent/finished/unknown，不等于撤销。网页只把已定义错误映射为结构化响应，不把底层完整异常或敏感路径发给页面。

## 6. 模板匹配与句柄

### 6.1 算法固定行为

1. 使用原始 PNG 截图和模板解码为一致的 8-bit RGB；灰度仅用于候选定位，颜色信息保留用于颜色验证；不使用 JPEG 预览参与匹配。
2. 首版模板必须为不透明 PNG；拒绝透明蒙版，避免未定义的 alpha 行为。由网页框选生成的模板天然满足此约束。
3. ROI 必须为四个整数且完全落在截图内；不静默裁切非法 ROI。
4. 使用 `TM_CCOEFF_NORMED`，默认 confidence=0.90，可接受范围为 (0,1]；该值为本地初值，不宣称内网默认值相同。
5. 灰度标准差小于 2（0–255 标度）的模板拒绝，防止低纹理相关性退化；拒绝非有限结果。
6. 在分数矩阵提取达到阈值的局部峰值；同分按 y/x 决定顺序。候选峰值超过 2000 则报 MatchOverflow，要求收缩 ROI 或提高阈值，不无限运行 NMS。
7. NMS 按分数降序、同分 y/x 顺序处理，IoU > 0.30 时抑制；最终匹配超过 100 报 MatchOverflow，不能静默丢弃以改变 index。
8. 全部结果还原为全截图坐标，再按 (y,x) 排序；返回 x/y/width/height/confidence，center 以边界中心定义。
9. 颜色验证（Q3）：灰度相关性产生的每个候选，在其匹配区域内与模板做颜色差异校验——色差低于颜色阈值判为同一外观；形状相同、状态色不同的候选判为不匹配。颜色阈值与容差由固定样本校准：同色轻微渲染差异须接受，不同状态色须拒绝；两类样本各自成组验收。

算法初值统一集中在 matcher 模块；除 Target.confidence/region 外不增加用户调参入口。灰度标准差阈值 2、NMS IoU 0.30、句柄 IoU 0.5、颜色阈值与 2 像素中心容差均为样本校准初值，不是已证明最优值；校准样本集固定后如需调整，必须记录依据。固定合成样本应精确验证坐标。

### 6.2 IMAGE 点击与句柄校验

IMAGE Target 每次点击重新采集、匹配和选择，在同一个设备操作事务中完成，防止本工具其他请求插队。采集完成至发送点击超过 1 秒则再采集一次；第二次仍过期则失败，不无限重试。

IMAGE handle 保存模板内容摘要、原 ROI/阈值/index、原 bounds、屏幕尺寸/方向及 session generation。再次使用时重新匹配；同一选择结果必须与原 bounds 的 IoU >= 0.5，且没有身份歧义，否则 StaleHandle。目标移动后要求调用者重新查找，这比误点另一个列表项更可诊断。

该规则只能降低误关联，不能证明两个外观相同且处于同一位置的业务对象身份相同。业务正确性由后置断言证明；不能将模板相似度解释为业务身份认证。

原生句柄也保留 locator、节点关键属性和 bounds；重复定位后关键属性不一致或会话改变则失败，不将过期节点转为任意新节点。

### 6.3 资产格式

每个模板由模板逻辑名引用：`Target.value` 为资产根目录内的逻辑相对路径（如 `buttons/confirm.png`）。物理存储按版本不可变（Template Version）：每个版本是一份完整图片文件及配套元数据（schema_version=1），包含 image_sha256、width、height、source_width/source_height、orientation、app_package、capture_time、可选主题/语言/显示缩放备注。

逻辑路径经清单解析到当前版本文件；规范化后检查仍在根目录内，拒绝路径穿越、符号链接/重解析点逃逸和外部绝对路径。加载时校验摘要和尺寸。没有元数据的内网旧模板允许在专门导入步骤中登记后使用，不能运行时编造采集环境。

新建默认拒绝同名。更新在资产级锁内执行：锁内先校验旧摘要，再将新图片与新元数据完整写入独立版本位置，全部落盘并校验通过后才原子更新清单指向新版本；任一步失败则旧版本保持可见。图片与元数据不分开替换，不存在“新图片配旧摘要”的中间状态；旧句柄引用的版本含义不变。不自动删除旧版本模板或证据。

## 7. 并发、取消与设备生命周期

### 7.1 会话状态

NEW → READY → LEASED → DRAINING → READY；关闭进入 CLOSED；无法确认在途操作结果进入 FAULTED。状态转换及操作原因写入结构化诊断，但不记录输入文本。

每个 DeviceSession 使用一个单 worker 的 ThreadPoolExecutor。完整的截图—匹配—点击作为一个队列任务；轮询等待则拆成短查询，每轮 await 间允许取消。提交前检查会话状态，排队但未开始的任务可取消，运行中的 Future 保留引用并观察最终结果。

每个操作携带取消标记（见 GLOSSARY“操作”与“发送阶段”）：线程任务在进入设备发送阶段（真实 click/input 等设备调用）前，在锁内执行最后一次取消检查——已取消则不发送并返回取消结果；发送一旦开始，取消只作用于等待方，操作结果记为 unknown。“匹配完成后、点击发送前取消”为必测路径，该窗口内不得发出点击。

不把绑定 event loop 的 asyncio.Lock 放在 pytest session fixture 中跨 loop 复用。设备队列和跨进程锁由长期存活的同步会话对象持有，当前调用在自己的 event loop 等待 Future。测试覆盖 session fixture 与 function loop 的组合。

### 7.2 跨进程租约

首版仅支持 Windows，使用标准库 msvcrt 的文件区域锁；锁文件保留在用户本机统一目录下，以设备序列号摘要命名。所有项目进程使用同一目录，不能各自在工作目录中加锁造成失效。

获得锁后才可执行设备请求。测试在整条用例期间持锁，网页每次快照/验证事务短时持锁，忙时立即返回 busy。session 级设备对象不等于 session 级永久占用。网页后台帧采集每次最多持有一个采集事务，不排队抢占测试。

锁是 OS 句柄的生命周期，不通过删除 lock 文件或 PID 过期时间偷锁；不提供强制抢占按钮。首版限制同一 Windows 登录用户运行，跨用户/远程使用不在保证范围。filelock 可用但不选为额外依赖，避免引入其 Windows 清理行为与额外配置。

### 7.3 取消和超时

- 默认 RPC 预算 5 秒，截图预算 5 秒；配置到支持超时的底层调用，不以 asyncio.wait_for 假装能中断同步线程。
- wait 类的整体预算独立计算，底层预算不超过剩余时间；整体超时后不再发起新轮询。
- 已发送的 click/input 被取消时，结果可标为 unknown，不再自动发送相同动作。
- close 最多等待 10 秒排空；超过后抛 SessionFaulted，保持会话故障与租约，不宣称设备可复用。
- 挂起线程无法安全强杀；故障影响范围按 Q1 限定为宿主进程：会话进入 FAULTED 后拒绝后续设备操作，失败钩子只保存已有画面、操作记录与“无法采集”原因，不再调用挂起的设备线程取证。恢复由操作者确认设备与 App 状态后重启宿主进程；进程退出释放主机锁，但不能撤销设备已接受的请求。close 超时不等于进程可退出：仍在运行的线程可能延迟解释器退出，文档与实现不得混用这两个概念。

如果固定版本 u2 的关键调用无法遵守底层网络超时，真机可靠性验收阻塞；不在未经批准时悄悄增加常驻子进程设备代理。届时单独评估进程隔离路线，披露其复杂度和仍不能撤销设备端操作的限制。

## 8. 网页服务与接口

### 8.1 页面布局

单页双栏：左侧画面，右侧“控件 / 图像”两个标签；顶部显示设备、App、占用状态和时间。底部显示当前 Target 与匹配结果。

默认“查看”；原生拾取和图像框选均冻结画面，不触发设备点击。P0 不提供点击/滑动控制 API。树不可用只禁用控件标签，不禁止图像制作。错误提示使用操作语言，不向测试人员暴露线程池等实现细节。

### 8.2 HTTP 接口 v1

| 接口 | 输入/输出与行为 |
| :--- | :--- |
| GET /api/v1/status | 服务版本、能力标志、设备连接/占用与当前截图状态；不返回口令和完整本机路径 |
| POST /api/v1/snapshots | 请求 screenshot 或 screenshot+tree；返回 snapshot_id、width/height、orientation、各采集时间、树错误；成功快照图像固定不再变 |
| GET /api/v1/snapshots/{id}/image | 原始 PNG；HTTP 禁止客户端当作实时缓存复用 |
| GET /api/v1/snapshots/{id}/tree | 归一化节点、父子关系、bounds 与属性；树不存在返回明确能力错误 |
| POST /api/v1/locators/validate | 接收结构化 Target 字段；重新采集并返回新 snapshot_id、count、matches 和生成表达式 |
| POST /api/v1/templates | 从服务端保留的 snapshot_id 与裁剪区域创建 PNG/元数据，不接受浏览器重压缩图片冒充原图 |
| PUT /api/v1/templates/{id} | 明确确认更新、期望旧摘要与新裁剪参数；摘要不符返回冲突 |
| GET /api/v1/templates | 列出资产相对路径、摘要、尺寸及元数据，不暴露任意文件 |
| POST /api/v1/templates/validate | 接收 IMAGE Target 数据，使用新截图验证，返回全截图坐标、分数及 snapshot_id |
| GET /api/v1/evidence/{id} | 只读取运行清单登记的证据条目：驱动/测试进程把失败证据写入 artifacts 目录并登记运行清单，网页仅加载清单内条目；运行清单是唯一入口，不提供任意文件读取 |
| WS /api/v1/preview | 推送状态元数据与 JPEG 预览帧；最多 2 客户端，单采集任务共享最新帧 |

请求/响应使用 Pydantic 校验，Target.by 为枚举字符串、index/region 为严格类型；Python 表达式只由服务端生成供复制，服务端不执行表达式。

HTTP 错误映射：输入 422，未知资源 404，占用/陈旧/摘要冲突 409，不支持 400，设备不可用 503，采集超时 504；响应包含 code、用户可读 message 和 request_id。匹配无结果是成功查询返回 count=0，不是服务器错误。

### 8.3 快照一致性与资源上限

快照为非原子截图/树组合，各自带时间。两者采集完成时间相差超过 1 秒显示不一致并禁止原生拾取，允许重新刷新或切换图像模式。不能仅凭时间相近保证页面相同，用户仍需确认画面。

单张输入最多 1200 万像素、文件 20 MiB；实时查看快照最多 3 份、总编码数据最多 64 MiB、有效期 120 秒。进入拾取/模板编辑的冻结快照被固定引用：不受 3 份上限驱逐，直到用户退出编辑或超时（10 分钟，校准初值）释放；驱逐只作用于未被固定的实时快照。内存预算同时覆盖解码图像、灰度/颜色中间缓冲与匹配工作区；匹配作业并发上限 1（与设备队列一致），超限拒绝并提示，不排队积压。内存驱逐不删除已保存模板或证据。输入大小限制在解码前后都检查。

预览默认 1 fps，只保留最新帧，慢客户端丢弃旧帧、不堆积。UI 树按需采集；浏览器页隐藏时停止请求，恢复后显示新鲜度。10 倍客户端不扩展设备并行度，超过 2 个客户端拒绝额外预览并说明限制。

FastAPI lifespan 创建和关闭服务持有的会话；StaticFiles 提供本地前端资源。限制 Host 与 Origin 为服务绑定的本机来源，WS 同样检查；浏览器变更请求要求同源并使用服务下发的会话校验值，不在 URL 中暴露该值。没有外部账号、API Key 或云模型配置。

## 9. 配置、命令与依赖

### 9.1 配置表（新本地设计值）

| 名称 | 默认与来源 |
| :--- | :--- |
| MOBILE_DEVICE_SERIAL | 沿用内网名称；空时只允许唯一已授权在线设备 |
| MOBILE_APP_PACKAGE / MOBILE_APP_ACTIVITY | 沿用内网名称；启动前必须具备 package，activity 可由设备解析 |
| 项目 mobile-auto.toml | 可选单一配置文件；路径由 --config 指定，无文件时使用默认值 |
| assets_dir / artifacts_dir | 相对于配置文件目录；默认 assets 和 artifacts，无配置则相对工作目录 |
| launch_ready_target | 默认无；仅描述 App 启动就绪（Q2），调用 wait_for_load 时没有配置立即报错，不自动推断，不承载业务页面就绪 |
| allowed_deeplink_schemes | 默认空；未授权任何链接 scheme，只能启动配置 App |
| wait_timeout / poll_interval | 默认 10 秒 / 0.5 秒；校验为合理有限值 |
| 服务地址 | 固定 127.0.0.1，默认端口 8765；只提供 --port 覆盖，不提供 --host 远程开关 |

优先级：显式 Python 构造参数或命令选项 > 三个既有环境变量 > TOML > 默认值。图像阈值按 Target 覆盖算法默认值。新增环境变量为 0；额外配置不修改内网 config 模块，绑定层按需转换。

### 9.2 命令面

只新增一个 console entry `mobile-auto`，包含 doctor 和 serve 两个子命令。doctor 仅执行无副作用检查（依赖版本、ADB 可见性、已授权设备在线状态）；任何可能触发设备端组件初始化或写入的主动探测单独列为显式步骤，doctor 不执行。缺依赖返回非零，不自动安装 APK、下载或修改设备。serve 启动网页服务；驱动示例通过 pytest 运行，不另造用例执行器。

### 9.3 依赖与锁定

- 核心：uiautomator2、Pillow；图像 extra：opencv-python-headless、NumPy；网页 extra：FastAPI、Uvicorn。
- 开发验证：pytest、pytest-asyncio、httpx、build、Ruff；不同时安装 opencv-python 与 headless。内网接入不得以本包依赖覆盖既有 OpenCV 发行包：绑定环境的依赖组合（既有 cv2 与本包图像 extra 的关系）单独验证，未通过前不声明内网可安装。
- 标准库处理 TOML、命令行、线程、文件锁和资源分发；不添加数据库、Redis、Celery 或图像云服务。
- 以 Python 3.12 为首个本地测试版本；内网 Python 版本由接入负责人确认，未通过相应 wheel 与测试前不扩展支持声明。
- 实施时在隔离虚拟环境中解析可用版本，生成精确锁定清单后测试；发布仅使用测试通过的锁定版本。当前未安装的包不凭空指定“已验证版本”。版本选择遵守 Python 3.12 wheel 可用、无已知阻塞兼容错误、所需官方 API 存在三项准入条件。

外部账号和 API Key 要求：无。外网只可能用于开发环境获得依赖；运行时所有前端、模板和识别均在本地。ADB 与 u2 设备端组件须在交付清单登记；安装及设备初始化由操作者明确执行，不藏在导入包或 doctor 中。

## 10. 文件级实施划分

以下是实现时的目标文件，不在本轮创建源码，也不将每个文件拆成独立任务。

| 文件或目录 | 职责 |
| :--- | :--- |
| pyproject.toml | 包名、Python 范围、extras、入口、测试及 lint 配置 |
| requirements-lock.txt | 经验证的目标环境精确依赖清单；发布需与测试记录一致 |
| src/mobile_auto/__init__.py | 有限公共导出，导入时无设备副作用 |
| src/mobile_auto/contracts.py | 本地 Target、Locator、Handle 和配置/结果基础类型 |
| src/mobile_auto/config.py | TOML、环境与参数合并、路径解析、校验 |
| src/mobile_auto/errors.py | 已列出的可诊断错误 |
| src/mobile_auto/session.py | 设备队列、跨进程租约、状态和所有权 |
| src/mobile_auto/backend_u2.py | u2/ADB 的有限调用及超时设置 |
| src/mobile_auto/selectors.py | 原生 Target 解析、匹配选择、bounds 与句柄校验 |
| src/mobile_auto/adapter.py | 异步方法与核心语义，不导入 FastAPI |
| src/mobile_auto/image.py | 模板匹配、ROI、峰值、NMS、排序 |
| src/mobile_auto/assets.py | 模板资产路径、PNG、摘要和元数据 |
| src/mobile_auto/evidence.py | 证据收集、采集失败归因、目录清单 |
| src/mobile_auto/cli.py | doctor / serve；网页依赖按需导入 |
| src/mobile_auto/viewer/app.py | FastAPI lifespan、路由、预览共享与来源检查 |
| src/mobile_auto/viewer/schemas.py | HTTP 输入输出与错误映射 |
| src/mobile_auto/viewer/static/ | index.html、app.js、styles.css；单一页面与坐标转换 |
| tests/unit/ | 选择器、图像、配置、状态与句柄测试 |
| tests/contract/ | 15 方法清单及真实 Port 绑定的检查入口 |
| tests/integration/ | 进程争用、真实 Future 取消、HTTP/WS 与生命周期 |
| tests/device/ | 需要真机的明确标记测试；无设备不计为通过 |
| tests/fixtures/ | 合成截图、模板及固定期望；不存公司业务截图 |
| examples/ | 原生流程、IMAGE 流程与 session/function fixture 示例 |
| docs/integration.md | 内网类型绑定骨架、契约测试模板、契约版本记录、fixture 生命周期、错误映射、引擎对照与 OpenCV 共存验证说明 |
| docs/validation.md | 环境、命令、测试结果与未覆盖范围 |
| docs/offline-install.md | 主机/设备端依赖、版本、许可与安装流程 |

真实 schema 到位后才新增面向其定义的列表绑定文件及测试，不为了满足文件清单编造 ListItemSchema。长按业务流程也不创建在底层包中。

## 11. 分阶段开发与预计投入

估算以一名熟悉 Python 的开发者、可用 Android 测试机和一枚可取得的现成演示 App 为前提（Q4：不自建 Android App；演示 App 的来源、版本、摘要与场景覆盖在阶段 1 固定并登记，当前候选未核验），不包含采购设备、获取内网审批和等待 schema 的日历时间。总计约 24–35 个开发工作日；演示 App 覆盖度确认前该数字仅为粗估。真机相关证据分三类分别记录：真机操作（演示 App）、图像算法（固定截图样本）、取消与竞态（模拟设备），互不替代；演示 App 覆盖不了的真机场景明确列为未验证。

| 阶段 | 具体工作 | 独立可用交付 | 预计 |
| :--- | :--- | :--- | :--- |
| 1 原生驱动 | 包与配置、doctor、设备队列/租约、原生定位和基本操作、PNG 截图、原生用例与失败证据 | 不依赖网页即可运行 Android 用例；标明缺列表绑定，无全 Port 兼容声明 | 8–11 日 |
| 2 模板定位 | 匹配器、资产格式、IMAGE 查询/等待/点击、过期句柄、正负样本和图像流程 | 无 UI 树目标也可通过显式模板进行定位操作 | 5–7 日 |
| 3 原生网页 | FastAPI、静态页、预览、冻结快照、UI 树、高亮、Target 生成及验证、占用提示 | 可独立作为原生元素 Inspector 使用，产物直接用于阶段 1 | 5–7 日 |
| 4 图像网页与移交 | 框选/保存/验证、资产迁移、打包资源、干净环境安装、接入手册和完整本地验收 | 用户完成两条定位闭环，形成可移交的 P0 本地包 | 6–10 日 |

阶段 1/2 均包含自身错误处理和测试，不把质量全部推迟到阶段 4。阶段 3 不依赖图像编辑 UI 才能使用。各阶段使用相同公开契约，不要求后续阶段对前面进行破坏性迁移。

内网完整绑定单列为受条件约束的集成工作，不进入上述完成日期：收到完整 schema 和行为样例后实现列表绑定、跑真实 fixture 与 Web 回归。不能将此工作写成“与前面类似”并算作已规划完成；其明确输入和出口见第十三节。

任务管理：批准后按上述四个阶段拆为 `.scratch/mobile-foundation/issues/01-*.md` 等独立任务，每个任务列输入、文件、行为、测试和退出条件。本轮不创建任务空壳，不初始化 Git；需要 Git 或提交时另遵循用户授权。

## 12. 验证方案与执行命令

以下命令是实施后在项目虚拟环境中的交接命令，本轮未执行；文件与入口随对应阶段交付。安装时使用项目解释器的 `python -m pip`，不使用全局 pip。

环境准备：

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -e ".[image,viewer,dev]"
```

基础检查：

```bash
.venv/Scripts/python -m ruff check src tests examples
```

```bash
.venv/Scripts/python -m pytest tests/unit tests/contract tests/integration -q
```

```bash
.venv/Scripts/python -m mobile_auto.cli doctor
```

真机测试通过测试配置文件指定设备与 App，不在命令中记录敏感信息：

```bash
.venv/Scripts/python -m pytest tests/device -m device --mobile-config tests/device/local.toml -q
```

测试包实现专用 `--mobile-config` pytest 选项；`local.toml` 为操作者的设备/App 测试配置，示例提供字段说明，不提交账号或设备私密数据。无该文件或无设备的任务显示未执行，不能作为最终真机验收通过。

构建与依赖：

```bash
.venv/Scripts/python -m build
```

```bash
.venv/Scripts/python -m pip check
```

发布前在新的虚拟环境安装生成的 wheel，并再次验证包内静态文件、CLI、图像样本和两条真机路径。网页启动由当前可用浏览器工具读取项目启动配置执行；不在本轮启动服务。验证页面时使用当前可用的 agent-browser 或浏览器插件，不将其作为产品用户的安装前提。

### 12.1 必测路径

| 测试域 | 正常 | 失败 | 边界 |
| :--- | :--- | :--- | :--- |
| 原生查询 | 各 Locator、单匹配、多元素枚举 | CSS、非法 JSON、无节点、断连 | 空文本、index 越界、重叠 bounds、XPath 句柄 |
| 操作 | 替换输入、启动、返回、按键、滚动 | 不可编辑、无 launch_ready_target、错误 scheme | amount 过大、空输入、零超时、重复 close |
| 图像 | 单/多目标、ROI、NMS、颜色验证通过、中心点击 | 无目标、损坏/纯色模板、IMAGE 文本操作、颜色验证不通过 | 边缘 ROI、同分、峰值溢出、非有限分数、方向变化、同色轻微渲染差异（须接受）、形状相同状态色不同（须拒绝） |
| 句柄 | 同一会话重新定位 | 跨会话、模板摘要变化、过期节点 | 列表插入/删除后 index 指向变化，不能误点 |
| 并发 | 一次事务串行、测试与网页轮换 | 两进程争用、挂起 RPC | 取消排队任务、取消已发送输入、匹配完成后点击发送前取消（不得发出点击）、会话故障后取证不调用设备、loop 切换、恢复后重连 |
| 证据 | 失败时 PNG/tree/匹配上下文齐全 | 树/截图采集失败 | 初始化阶段无设备，保留原始错误，输入内容不进日志 |
| 网页 | 拾取/框选/验证/复制/保存 | 非同源、旧摘要更新、设备忙 | 缩放留白、旋转、树为空但 IMAGE 可用、快照过期、慢 WS |
| 打包 | wheel 安装可启动并带静态资源 | 缺 extra、ADB、设备端组件 | 中文/空格路径、模板迁移、没有外网 |

图像回归包含固定合成数据和至少 10 组真实正负样本；真机原生流程和 IMAGE 流程各重复 30 次，逐次归因，零未解决工具错误才可通过，不将次数转化为长期可用率承诺。

浏览器人工验收分别完成：原生拾取到 pytest 断言、无 UI 树时模板制作到 IMAGE 点击、测试占用时网页暂停采集、失败后区分原操作截图与事后截图。仅页面截图或控制台无报错不构成功能通过。

## 13. 内网交接门槛与未知事项责任

这些事项明确延后到内网集成，原因是源码不可出网；它们不妨碍前四个独立交付，但阻止“完整 Port 集成已通过”的声明。

| 输入/事项 | 负责方 | 必需证据 | 未提供时的状态 |
| :--- | :--- | :--- | :--- |
| 真实 Port/Target 的默认值、异常与 marker 默认行为 | 内网框架维护者 | 允许范围内的签名/字段表和行为测试结果 | 独立核心可交付，兼容声明受限 |
| ListItemSchema | 内网框架维护者 | 完整字段、字段默认值、作用域与嵌套规则；至少含普通行、缺字段、空列表和 max_items 的输入输出样例 | 不绑定 extract_list，不写虚构 schema，不发布完整适配器完成声明 |
| 原有 ImageMatcher | 内网接入执行者 | 使用本包样本跑出的 ROI、分数、NMS 与排序结果 | 默认使用本地引擎；接入旧引擎未验收 |
| 内网 Python/设备/安装策略 | 内网环境负责人 | 版本、架构、传入方式与允许的设备初始化步骤 | 本地锁文件不能宣称适用于内网 |
| BYD B 类流程及 Web 回归 | 内网测试维护者 | 真实页面用例与接入前后结果 | 不计入本项目已验证成果 |

收到 schema 后的明确绑定步骤：用真实类型建立契约测试，将 container 和行/字段范围转换为已有原生查询操作，按 schema 规定提取文本/属性与缺失值，验证 max_items 和空列表，再运行真实页面样例。若 schema 包含首版不支持的滚动加载、嵌套 IMAGE/OCR 或 JS 语义，须明确扩大范围或报告不支持，不能随意改其含义。

这里没有要求导出公司完整源码或私密数据；可由内网人员执行测试并反馈脱敏行为差异。

## 14. 简化检查、失败处理与回退

### 14.1 每个防护的具体原因

- 兼容层：内网真实类型无法访问，避免业务契约污染执行核心。
- 设备队列与租约：同步设备请求和多个进程会竞争同一设备。
- FAULTED 状态：无法取消的在途操作结果未知。
- 模板摘要与句柄位置检查：旧模板和旧 index 可能点击错误对象。
- ROI/大小/峰值上限：限制无效输入和图像算法计算量。
- 本地原始证据：无法靠网页快照解释操作时现场。

不增加驱动自动重连并续跑、不增加多个匹配器自动回退、不增加后台设备保活服务、不新增业务录制状态机。若复杂度主要来自无法满足的 u2 取消语义，应改变隔离策略或承诺边界，而不是层层重试。

### 14.2 依赖失败与规模检查

- u2 或设备不可用：操作失败并留证；不能用缓存图像继续点击。
- UI 树不可用：只关闭语义拾取，图像路径保留；不自动切换调用者的定位策略。
- 网页停止：Python 驱动仍可运行，核心不依赖网页生命周期。
- 模板数量或画面尺寸增大十倍：每次只加载指定模板，不遍历模板库；图像大小和候选上限限制计算，拒绝超限而不是占满内存。
- 用户/客户端增大十倍：单设备并发拒绝，不假装支持共享控制；设备池属于另一个范围。

### 14.3 回退和授权

本方案只产生新的包、资产和本地证据，不迁移已有业务数据库。内网接入前保存原配置与依赖清单；出现回归时停用新绑定、恢复此前装配方式和依赖版本，原有 WebAdapter 不变。保留新产生模板与证据，不自动删除。

真机点击、输入和 App 启动可能改变测试 App 状态，不能靠卸载 Python 包回退；使用测试数据，并由示例/业务维护者明确清理步骤。所有删除、Git 初始化后的 commit/push、外部发包和内网部署均按用户授权执行，不因方案批准自动扩大权限。

实施批准只授权按所选阶段写代码和验证；完整离线包、外部发布或真实业务数据操作需要对应范围的授权。本轮没有这些操作。

## 15. 来源与证据等级

查询日期均为 2026-10-24。以下是通过 WebSearch 获取的官方页面及摘要；WebFetch 直读失败，不能视为对固定版本源码的完整审查。

- uiautomator2 官方 README：https://github.com/openatx/uiautomator2/blob/master/README.md
- uiautomator2 XPath：https://github.com/openatx/uiautomator2/blob/master/XPATH.md
- screenshot 源码搜索定位：https://github.com/openatx/uiautomator2/blob/master/uiautomator2/__init__.py
- OpenCV 模板匹配：https://docs.opencv.org/4.13.0/d4/dc6/tutorial_py_template_matching.html
- OpenCV API：https://docs.opencv.org/4.13.0/df/dfb/group__imgproc__object.html
- Python Future 取消：https://docs.python.org/3.12/library/concurrent.futures.html
- Python asyncio：https://docs.python.org/3.12/library/asyncio-task.html
- FastAPI lifespan：https://fastapi.tiangolo.com/advanced/events/
- FastAPI StaticFiles：https://fastapi.tiangolo.com/tutorial/static-files/
- FastAPI WebSocket：https://fastapi.tiangolo.com/advanced/websockets/

未能读取 uiautodev 实现，因此本方案没有把其内部协议、坐标算法或线程模型作为已核验依据。ADB、目标 App、u2 RPC 超时行为、真实内网接口与最终 wheel 组合都保留明确验收门槛。

## 16. 批准内容与交接摘要

请求批准的是：独立 Python 核心、原生/IMAGE 两种定位（含颜色验证）、一个本机网页服务、四个可独立使用的开发阶段，R02/R04/R05/R07 对 PRD 的定向修订，以及第二轮复审决定 Q1–Q4（挂起故障影响范围为宿主进程、`wait_for_load` 仅启动就绪、图像匹配区分颜色、不自建 Android 测试 App）。本轮修订已同步至本方案、PRD 与 GLOSSARY.md；仍未开始编码。

完整方案不以取得内网源码为前提，也不把缺失 schema 伪装成已经解决。按本方案执行的工程师应先完成阶段 1 的原生驱动和验证，再扩展图像与网页；最终本地验收和内网集成分别报告。

用户若仅批准设计，保留本方案作为执行依据，不开始编码。用户明确要求“按方案实现”时，再进入相应阶段，不重复征求相同方向的批准。
