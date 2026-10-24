# mobile-auto

Android 移动能力包:原生与图像定位驱动、scrcpy 视频流调试网页、失败证据采集。
交付形态为**源码**(不打包 wheel);面向内网 pytest 体系的接入指南见 `docs/integration.md`(本地保留)。

## 快速开始

```bash
pip install -e ".[image,viewer,stream,dev]"   # stream 为 P1 视频流/控制(可选)
python scripts/fetch_scrcpy_server.py          # 幂等校验随包 scrcpy 资产(已随仓库提供)
mobile-auto doctor                             # 环境检查(ADB 可见性/设备授权)
python -m pytest tests/unit -q                 # 单元与契约测试
```

- ADB 随包在 `tools/adb/`(platform-tools 37.0.1 的 adb 子集),优先于 adbutils 内置与 PATH。
- 真机测试:`python -m pytest tests/device -m device --mobile-config tests/device/local.toml`
  (配置示例 `tests/device/local.toml.example`;`local.toml` 不入库)。
- 失败证据钩子(opt-in):`pytest --mobile-evidence`。

## 项目结构(按文件分工)

```
src/mobile_auto/
├── __init__.py            # 公共 API 出口:DeviceSession/MobileAdapter/MobileConfig/
│                          #   Locator/Target/ElementHandle + 错误类型全集
├── contracts.py           # 驱动契约:Locator/Target/ElementHandle 参考形状与句柄重校验
├── errors.py              # 错误分类体系(唯一权威;viewer HTTP 状态按此映射)
├── config.py              # MobileConfig 与 load_config(显式参数 > 环境变量 > TOML > 默认)
├── cli.py                 # 命令行入口:doctor / serve
│
├── core/                  # ── 执行核心(设备访问必经 session 串行队列)──
│   ├── session.py         #   DeviceSession:状态机、操作队列与取消语义、
│   │                      #   截图双通道策略(u2/scrcpy/auto)、通道回收
│   ├── lease.py           #   跨进程设备租约(Windows 文件区域锁)
│   ├── backend.py         #   DeviceBackend 协议 + U2Backend + ADB 枚举与随包优先级
│   ├── adapter.py         #   MobileAdapter:15 个异步方法的实现与轮询/事务语义
│   ├── selectors.py       #   定位选择规则:唯一性/index/可见性/句柄重校验
│   └── ui_tree.py         #   uiautomator dump XML 解析为 UiNode 树
│
├── imaging/               # ── 图像定位 ──
│   ├── engine.py          #   模板匹配引擎:多尺度、NMS、HSV 颜色验证
│   └── store.py           #   模板资产库:版本不可变、清单原子切换、摘要校验
│
├── stream/                # ── scrcpy 视频流/控制通道(P1)──
│   ├── channel.py         #   ScrcpyChannel:server 推送启停、CAPTURE 截图、
│   │                      #   视频帧获取、像素坐标触摸注入、wm size 权威屏幕尺寸
│   └── assets/            #   随包 server jar + libjpeg-turbo(来源/摘要见 PROVENANCE.md)
│
├── evidence/              # ── 失败证据 ──
│   ├── collector.py       #   采集落盘 + 运行清单(读取唯一入口);视频帧优先
│   └── pytest_plugin.py   #   --mobile-evidence 钩子(不开零行为)
│
└── viewer/                # ── 本机网页调试服务(仅监听回环)──
    ├── app.py             #   FastAPI:快照/拾取/模板/验证端点、预览 WS、
    │                      #   通道管理器(懒启动/退避/空闲自停)、直接控制端点
    ├── store.py           #   快照库(容量驱逐与冻结引用)
    ├── candidates.py      #   拾取候选生成(五种原生定位 + XPath)
    └── static/            #   前端:index/app/coords(坐标纯函数,node 可测)
```

```
tools/adb/                 # 随包 ADB(platform-tools 37.0.1 adb 子集 + 许可文本)
scripts/
├── fetch_scrcpy_server.py # 从 wetest-scrcpy==0.3.8 wheel 提取 server 资产(幂等)
└── bench_image.py         # 图像引擎基准/校准脚本
examples/                  # native_flow / image_flow / evidence_flow 接入示例
tests/
├── unit/                  # 单元(伪造设备/伪造通道,不碰 adb)+ node 纯函数测试
├── contract/              # 15 方法契约核对
├── integration/           # 两进程租约争用
└── device/                # 真机用例(--mobile-config 门控;local.toml 不入库)
```

## 关键运行约定

- **设备访问必经 `DeviceSession`**:单 worker 队列串行;跨进程互斥靠设备租约
  (`LEASED` 即他人持有,网页只读降级)。
- **截图通道**由配置 `screenshot_source = auto|u2|scrcpy` 决定(默认 auto:
  scrcpy 优先、连续 3 次失败降级 u2 60s)。
- **网页直接控制**默认关闭,`POST /api/v1/control` 显式启用;注入经租约门控。
- 许可证注意:`wetest-scrcpy` 的 PyPI 元数据未标注 License——stream extra 为
  条件交付,不装不影响其余能力(详见 `src/mobile_auto/stream/assets/PROVENANCE.md`)。
