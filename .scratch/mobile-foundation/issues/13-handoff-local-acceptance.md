# 13: 移交文档与本地验收

**What to build:** 本地交付可验收、内网可接入：接入指南（绑定骨架、契约测试模板、契约版本记录、错误映射、引擎对照、OpenCV 共存验证）、离线安装清单、验证记录文档；跑完整本地验收——三类证据齐备，真机流程 30 次重复逐次归因零未解决工具失败（spec 用户故事 32–36）。

**Blocked by:** 12 打包与干净环境安装

**Status:** resolved

- [x] 接入指南含绑定骨架与契约测试模板（15 方法清单核对入口）及契约版本记录
- [x] 引擎对照样例与步骤成文，供内网 ImageMatcher 用同一组样例对照
- [x] 离线安装清单列明主机与设备端依赖、版本、来源与许可证
- [x] 验证记录包含环境、命令、结果与未覆盖范围（含演示 App 未覆盖场景清单）
- [x] 真机原生流程与 IMAGE 流程各重复 30 次逐次归因，零未解决工具原因失败
- [x] 验收结论明确区分"本地验收通过"与"内网集成待验证"；extract_list 缺真实 schema 列为已知阻塞项


## Answer

交付(本工单提交):

- `docs/integration.md`:内网接入指南——15 方法参考契约表(语义逐条列明)、绑定层骨架代码(枚举归一化 + 异常映射 + 句柄包装要点)、契约测试模板与核对入口、13 类错误映射表、契约版本记录(2026-10-24 初版)、内网交接门槛(ListItemSchema/真实 Port 默认值/内网 Python)、引擎对照步骤(samples 12 组样例)、OpenCV 共存验证顺序。
- `docs/offline-install.md`:主机端依赖表(实测版本/来源/许可)、设备端组件(atx-agent/app-uiautomator*)、五步离线装机流程(wheel + pip download --no-index)、已知限制。
- `docs/validation.md`:验证记录——环境、全部命令与结果、30 次重复逐次归因(60/60 PASS,单轮 3.0–3.5s)、三类证据各自清单、未覆盖范围(extract_list/内网集成/演示 App 未覆盖场景/断网演练/非 headless OpenCV 共存/多端并发/xdist)、验收结论明确二分:本地验收通过,内网集成待验证。
- `requirements-lock.txt`:42 项精确依赖锁(实测环境 freeze)。
- `tests/device/test_reliability_30x.py`:30 次重复载具,逐次输出 "NATIVE/IMAGE n: PASS/FAIL 原因" 固定格式供归因引用。

30 次重复结果:NATIVE 30/30 PASS;IMAGE 30/30 PASS。过程归因:首轮 IMAGE 全 FAIL 为测试模板制作时页面未就绪(goto 后未 wait_for)——测试准备问题,修复后全过,不属工具缺陷;零未解决工具原因失败成立。

验证:ruff 0 问题;单元 228 项 + 真机 10 项 + xfail 1 项;打包与干净安装已在工单 12 记录。

决定与说明:
- 12 个 issue 全部 resolved;本地验收结论与未覆盖范围以 docs/validation.md 为准。
- 模板资产目录(tests/device/assets)随可靠性运行产生若干 reliability 模板,属运行产物,.gitignore 已排除 assets 内容(见 git status 核对)。
