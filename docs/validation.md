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

## 验收结论

- **本地验收:通过**。六项工单验收链(01–13)全部完成;lint/单元/契约/
  集成/真机/打包/干净安装/30 次重复全部达到 plan §12 命令口径。
- **内网集成:待验证**。绑定层实现、真实 Port 契约核对、引擎对照、
  OpenCV 共存与离线装机,须按 docs/integration.md 与 docs/offline-install.md
  在内网环境执行后另行出具结论。
