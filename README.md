# mobile-auto

Android 移动能力包:原生与图像定位驱动、scrcpy 视频流调试网页、失败证据采集、多设备并行与测试平台基础能力。
交付形态为**源码**(不打包 wheel);面向内网 pytest 体系的接入指南见 `docs/integration.md`。

## 快速开始

```bash
pip install -e ".[image,viewer,stream,dev]"   # stream 为视频流/控制(可选)
python scripts/fetch_scrcpy_server.py          # 幂等校验随包 scrcpy 资产(已随仓库提供)
mobile-auto doctor                             # 环境检查(ADB 可见性/设备授权)
python -m pytest tests/unit -q                 # 单元与契约测试
```

- ADB 随包在 `tools/adb/`(platform-tools 37.0.1 的 adb 子集),优先于 adbutils 内置与 PATH。
- 真机测试:`python -m pytest tests/device -m device --mobile-config tests/device/local.toml`
  (配置示例 `tests/device/local.toml.example`;`local.toml` 不入库)。
- 失败证据钩子(opt-in):`pytest --mobile-evidence`;运行期投屏(opt-in):`pytest --debug-viewer`。
- 多设备并行:`pytest -n 2 --mobile-devices=<serialA>,<serialB>`(设备池经跨进程租约协调,
  每 worker 各持一台,产物按 `artifacts/xdist-gwN/` 分目录)。

## 项目结构(按文件分工)

```
src/mobile_auto/
├── __init__.py            # 公共 API 出口:DeviceSession/MobileAdapter/MobileConfig/
│                          #   Locator/Target/ElementHandle + 错误类型全集
├── contracts.py           # 驱动契约:Locator/Target/ElementHandle 参考形状与句柄重校验
├── errors.py              # 错误分类体系(唯一权威;viewer HTTP 状态按此映射)
├── config.py              # MobileConfig 与 load_config(显式参数 > 环境变量 > TOML > 默认)
├── cli.py                 # 命令行入口:doctor / serve
├── pool.py                # 设备池:租约目录即协调器(非阻塞抢占/产物分目录/运行状态)
│
├── core/                  # ── 执行核心(设备访问必经 session 串行队列)──
│   ├── session.py         #   DeviceSession:状态机、操作队列与取消语义、
│   │                      #   截图双通道策略(u2/scrcpy/auto)、通道回收
│   ├── lease.py           #   跨进程设备租约(Windows 文件区域锁)+ 持有者信息/占用探测
│   ├── backend.py         #   DeviceBackend 协议 + U2Backend + ADB 枚举与随包优先级
│   ├── adapter.py         #   MobileAdapter:15 个异步方法的实现与轮询/事务语义
│   │                      #   + adapter.device 坐标手势扩展面(tap/swipe/long_press)
│   ├── device_ops.py      #   坐标手势原语(scrcpy 控制流优先、u2 回退;DOWN 后失败补 UP)
│   ├── selectors.py       #   定位选择规则:唯一性/index/可见性/句柄重校验
│   └── ui_tree.py         #   uiautomator dump XML 解析为 UiNode 树
│
├── imaging/               # ── 图像定位 ──
│   ├── engine.py          #   模板匹配引擎:多尺度、NMS、HSV 颜色验证、采集位置先验
│   └── store.py           #   模板资产库:版本不可变、清单原子切换、摘要校验
│
├── stream/                # ── scrcpy 视频流/控制通道(双后端)──
│   ├── __init__.py        #   channel_factory:按 config.stream_backend 选择后端
│   ├── upstream.py        #   UpstreamChannel:上游官方 server v5.0.1(默认;Android 17
│   │                      #   兼容,自研薄客户端,scid 编码宿主身份)
│   ├── upstream_proto.py  #   上游协议纯字节编解码(socket/控制消息/视频分包)
│   ├── channel.py         #   ScrcpyChannel:wetest 后端(server 推送启停、CAPTURE
│   │                      #   全分辨率截图、视频帧、触摸注入)
│   ├── selfheal.py        #   残留 server 自愈(双前缀扫描:ma- comm + scid cmdline;
│   │                      #   宿主已死才杀,活跃表保护同进程多通道)
│   ├── supervisor.py      #   ServerSupervisor:引用计数保活/退避/空闲自停/atexit
│   └── assets/            #   双后端 server 资产(来源/摘要见 PROVENANCE.md)
│
├── evidence/              # ── 失败证据 ──
│   ├── collector.py       #   采集落盘 + 运行清单(读取唯一入口);视频帧优先
│   └── pytest_plugin.py   #   --mobile-evidence / --debug-viewer / 设备池 fixture
│
└── viewer/                # ── 本机网页调试服务(仅监听回环)──
    ├── app.py             #   FastAPI:快照/拾取/模板/验证/设备档案/占用运行态/
    │                      #   证据浏览端点、预览 WS、直接控制(租约门控)
    ├── server.py          #   --debug-viewer 后台服务(controller 进程,端口探测)
    ├── store.py           #   快照库(容量驱逐与冻结引用)
    ├── candidates.py      #   拾取候选生成(五种原生定位 + XPath)
    └── static/            #   前端:index/app/coords/evidence/runs/快照冻结
```

```
tools/adb/                 # 随包 ADB(platform-tools 37.0.1 adb 子集 + 许可文本)
scripts/
├── fetch_scrcpy_server.py # 提取 wetest server 资产 + 下载上游 v5.0.1(幂等,摘要校验)
└── bench_image.py         # 图像引擎基准/校准脚本
examples/
├── native_flow / image_flow / evidence_flow   # 单设备接入示例
└── byd_academy/           # 真机用例套件:pytest fixture 平台注入(platform 标记/
                           #   platform+app fixture 端分发/--platform 旗标端选择)
tests/
├── unit/                  # 单元(伪造设备/伪造通道,不碰 adb)+ node 纯函数测试
├── contract/              # 15 方法契约核对
├── integration/           # 两进程租约争用
└── device/                # 真机用例(--mobile-config 门控;local.toml 不入库)
```

## 关键运行约定

- **设备访问必经 `DeviceSession`**:单 worker 队列串行;跨进程互斥靠设备租约
  (`LEASED` 即他人持有,网页只读降级)。设备池(`--mobile-devices` 或配置
  `device_pool`)复用同一租约目录做协调,不建中心服务。
- **视频通道后端**由配置 `stream_backend = upstream|wetest` 决定(默认 upstream:
  上游官方 server v5.0.1,Android 16 QPR2+/17 兼容,Apache-2.0;wetest 为内嵌协议
  fork 的回退选项,Android 17+ 起流即崩)。两后端同接口,由 `ServerSupervisor`
  统一保活(引用计数/退避/空闲自停),残留 server 自愈按双前缀扫描。
- **截图通道**由配置 `screenshot_source = auto|u2|scrcpy` 决定(默认 auto:
  scrcpy 优先、连续 3 次失败降级 u2 60s),与通道后端正交。注意:upstream 后端
  的取证截图(capture_png)分辨率=流分辨率(max_size 默认 1080);需全分辨率取证
  用 `screenshot_source="u2"`(详见 docs/prd-auto.md §8.5)。
- **网页直接控制**默认关闭,`POST /api/v1/control` 显式启用;注入经租约门控。
- **viewer 数据一致前提**:serve/--debug-viewer 与 pytest 运行共用同一 `--mobile-config`
  (artifacts 目录一致),占用/运行态/证据才能互相可见。
- 许可证:上游 scrcpy server v5.0.1 为 Apache-2.0(随包,PROVENANCE.md 登记);
  `wetest-scrcpy` 的 PyPI 元数据未标注 License——stream extra 为条件交付,
  不装不影响其余能力(详见 `src/mobile_auto/stream/assets/PROVENANCE.md`)。
