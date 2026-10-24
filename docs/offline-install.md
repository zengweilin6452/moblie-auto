# 离线安装清单(工单 13)

运行时全部能力本地化:前端/模板/识别均在主机本地,不发起外部网络请求
(工单 12 静态扫描验证)。外网仅用于开发环境获取依赖。

## 主机端依赖(经测试通过的精确版本,见 requirements-lock.txt)

| 组件 | 版本 | 来源/许可 | 说明 |
| :--- | :--- | :--- | :--- |
| Python | 3.12.10 | python.org / PSF | 首测版本;内网版本待接入负责人确认 |
| mobile-auto | 0.1.0 | 本仓库源码 / 内部 | 源码树即交付物;viewer static 随 src/,examples 与样本在仓库目录 |
| uiautomator2 | 3.7.1 | PyPI / MIT | 核心驱动;附 atx-agent 等设备端组件 |
| adbutils | 2.12.0 | PyPI / MIT | 随 u2 安装,自带 adb.exe(Windows) |
| Pillow | 13.0.0 | PyPI / MIT-CMU | PNG 编解码 |
| opencv-python-headless | 5.0.0 | PyPI / Apache-2.0 | 图像 extra;内网共存验证见 docs/integration.md §8 |
| numpy | 2.5.4 | PyPI / BSD-3 | 图像 extra |
| fastapi | 0.161.1 | PyPI / MIT | viewer extra |
| uvicorn | 0.30+ | PyPI / BSD-3 | viewer extra |
| websockets | 13+ | PyPI / BSD-3 | viewer extra(uvicorn WS 必需) |
| pytest / pytest-asyncio / httpx / build / ruff | 见锁文件 | PyPI / MIT 系 | dev extra,仅开发 |

锁定清单:`requirements-lock.txt`(42 项,与上述实测版本一致)。

## 设备端组件(uiautomator2 首次连接自动部署)

| 组件 | 说明 |
| :--- | :--- |
| atx-agent | 设备端守护进程,u2 推送 |
| app-uiautomator.apk | uiautomator 服务 APK |
| app-uiautomator-test.apk | 辅助测试 APK |

初始化是显式建立会话的一部分;doctor 不安装、不初始化(plan 9.2)。
完全离线内网需预置上述 APK/二进制并保证 u2 能从本地缓存取用,
安装流程由操作者按内网规范执行。

## 离线安装流程(源码交付,2026-10-24 决定:不打包 wheel)

1. 在有网环境:`pip download -r requirements-lock.txt -d wheels/`
   下载依赖轮子(含 hatchling,供可编辑安装的构建隔离使用)。
2. 拷贝**整个源码仓库** + wheels/ 至内网(直接使用源码,不产出、不安装 wheel)。
3. 内网主机:
   `python -m venv .venv && .venv/Scripts/python -m pip install --no-index --find-links wheels/ -e ".[image,viewer]"`
4. `pip check`、`.venv/Scripts/mobile-auto doctor` 与 `pytest tests/unit tests/contract tests/integration -q` 验证。
5. 设备端组件:首次真机连接由 u2 初始化(或按内网规范预装)。

注:pyproject 仅承载可编辑安装所需的包元数据;不执行 `python -m build`,
不产出 wheel/sdist(2026-10-24 此前曾验证过 wheel 路径,记录见 docs/validation.md,已不作为交付方式)。

## 已知限制

- 内网既有 opencv-python 共存需按 integration.md §8 单独验证后才装 image extra。
- 演示 App(系统设置)为设备预装;APK 摘要登记见 docs/demo-app.md。
- 无外部账号、API Key 或云服务依赖。
