# 12: 打包与干净环境安装

**What to build:** 能力包可移交：构建包含前端静态资源的 wheel；在全新虚拟环境安装后，CLI、原生示例用例、IMAGE 示例、图像样本与模板资产全部可用；模板与产物路径不依赖开发机（spec 用户故事 20、34 部分）。

**Blocked by:** 08 IMAGE 驱动操作；10 原生拾取与定位验证；11 图像模板制作闭环

**Status:** resolved

- [x] wheel 构建成功，包含前端静态资源与示例
- [x] 全新虚拟环境安装 wheel 后：doctor、原生示例、IMAGE 示例全部通过
- [x] 含中文或空格的路径下安装与运行正常
- [x] 模板资产相对路径迁移验证；运行时不发起外部网络请求（完整离线安装包另行验收）
- [x] 依赖锁定清单与 pip check 干净；image/viewer/dev extras 按需安装可用


## Answer

交付(本工单提交):

- pyproject:wheel force-include 打包 viewer/static(网页三件套)、examples/(原生与 IMAGE 示例)、tests/fixtures/images → mobile_auto/samples(图像样本集,供内网引擎对照)。
- examples/native_flow.py(--config 加载配置,启动→定位→点击→断言→截图)与 examples/image_flow.py(--template 相对路径,wait_for→点击→截图),均可 `python examples/xxx.py` 直接运行。

验证记录(全部实测):
- `python -m build --wheel` 成功;wheel 内容核验:static 3 件、examples 2 件、samples 14 件。
- 全新 venv(位于 `测试 目录/` 中文+空格路径)安装 wheel:doctor 退出码 0(adbutils 自带 adb、列出真机 749622c9);静态资源/样本/示例文件在包内齐全。
- extras:`pip install wheel[image,viewer]` 后 `pip check` 干净(No broken requirements)。
- 真机示例:clean venv 运行 native_flow.py 通过(启动→点击→断言→截图);image_flow.py 用迁移模板(studio/dbg-123600.png)点击通过。
- 模板资产迁移:开发机 assets 目录整体复制到新环境相对路径 demo-assets,TemplateStore 加载 12 个模板、sha 校验通过——换机后用例不失效(相对路径)。
- 离线性:包内代码无外部 CDN/registry 引用(静态扫描 none);doctor/示例/服务全部本地资源。完整离线安装包(含依赖 wheel 离线库)按计划另行验收(plan 8.2/R08)。

决定与说明:
- requirements-lock.txt 属工单 13 的验证记录与离线清单(发布锁文件随 13 出具)。
- 演示 App 的 APK 本体不入库不入包(105MB),摘要登记在 docs/demo-app.md(工单 02 决定);干净环境真机验收依赖目标机预装系统设置。
- 验证后已清理临时 venv 与演示目录,不留脏状态。


## Comments

- 2026-10-24(用户决定):本项目**不打包 wheel**,内网接入直接使用源码(源码树拷贝 + 可编辑安装)。本票的 wheel 构建与干净环境验证保留为能力记录(证明随时可打包),但交付路径以源码为准;offline-install.md 安装流程已相应改写,pyproject 中仅为 wheel 准备的 force-include 已移除。
