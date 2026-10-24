# 02: 设备会话与第一条真机用例

**What to build:** 移动测试工程师可以在配置的演示 App 上运行第一条真机用例:建立设备会话,按资源 ID 或文本定位原生控件,等待可见后点击,读取文本断言,采集 PNG 截图。本票同时固定演示 App 的来源、版本与摘要并登记场景清单(spec 用户故事 2、4、5、7 的首个切片)。

**Blocked by:** 01 包骨架与 doctor 环境自检

**Status:** resolved

- [x] 设备会话建立与关闭;无设备、多设备未选、序列号错误、未授权均为明确错误,不误选设备
- [x] resource_id 与 text 精确匹配定位;唯一匹配可点击,多匹配无序号报歧义
- [x] wait_for 返回 bool 且可取消;"未找到"与"设备错误"区分报告
- [x] screenshot 返回新采集的 PNG bytes,不返回缓存帧
- [x] 演示 App 的来源、版本、摘要与场景清单已登记入文档
- [x] 真机用例(启动 → 定位 → 点击 → 断言 → 截图)在演示 App 上通过;无设备时用例显示未执行、不计为通过

## Answer

交付(提交 60818b7):

- `src/mobile_auto/session.py`:DeviceSession(状态 NEW→READY→CLOSED,单 worker 串行队列,幂等关闭)与 resolve_serial 设备解析——无设备、多台未配置序列号、序列号不在线、未授权、offline 各有独立报错与专项测试;绝不代选设备。
- `src/mobile_auto/backend_u2.py`:DeviceBackend/BackendElement 协议(测试接缝)+ U2Backend 实现(u2 延迟导入);ADB 设备枚举从 cli 下沉至此,doctor 与会话共用同一实现。
- `src/mobile_auto/selectors.py`:定位方式校验、可见性过滤(非空 bounds 与屏幕相交)、index 语义、单目标歧义判定。
- `src/mobile_auto/adapter.py`:goto/click/get_text/wait_for/is_visible/find_elements/screenshot/close 异步接口。click/get_text 在查找预算内轮询等待(plan 5.3),每轮"定位-校验-动作"同一队列任务,歧义立即报;wait_for 拆短轮询、取消后不再发起新查询、设备错误原样传播不折算 False(有专项测试);goto 空串冷启动(stop 后再启动,保证起点确定),非空按白名单校验,空白名单立即报 ConfigurationError。
- `docs/demo-app.md`:演示 App 登记——Android 系统设置 v14/vc34,APK sha256 与大小、来源、测试机型号、场景清单、前置条件与已知设备特性。
- `tests/device/`:`--mobile-config` pytest 选项门控,无配置跳过(输出"未执行、不计为通过");local.toml.example 提供字段说明,local.toml 不入库。

验证结果:ruff 0 问题;单元测试 91 项全部通过;真机用例 2 项在演示 App 上通过(启动→wait_for WLAN→点击→wait_for 高级设置→get_text 断言→find_elements 句柄→PNG 截图);无配置时全套件显示 2 skipped。

决定与说明:

- wait_for 的 timeout 传负数/非有限值报 ValueError(调用方编程错误,不属 13 类设备错误);"未找到"(返回 False)与"设备错误"(抛原异常)路径分离,均有测试。
- goto 强制冷启动(先 stop 再启动):实测发现设置进程存活时列表保留滚动位置、WLAN 不在可见区,冷启动消除该非确定性;stop 语义在 docstring 与 demo-app.md 注明。
- 设备息屏属环境状态:真机夹具负责点亮屏幕并上滑解锁(假定无密码锁),登记于 demo-app.md;夹具唤醒是测试环境准备,不进入驱动契约。
- screenshot 的"内部记录采集时间与方向"延后到失败证据工单(05)——当前无消费方,不预建结构。
- resolve_serial 以 serial→state 字符串字典为接口,"device" 为 adb 协议字面值;DeviceEntry.authorized 属性保留给 doctor 报告使用。
- submit() 的 FAULTED 分支当前不可达(状态机完整转换随工单 04),作为防御性校验保留,模块 docstring 已标注该差距。
- code-review 双轴发现并已修复:click/get_text 等待语义缺失、goto 非空错误类别(改 ConfigurationError 对齐 plan 5.3)、offline 设备报错措辞、wait_for 设备错误传播无测试、105MB APK 与 UI 调试 XML 误入库(已移出版本库,仅文档登记摘要;历史已重写,未推送)。
- 真机运行证据记录于本 Answer 与 docs/demo-app.md;docs/validation.md 按计划属移交工单(13),不提前创建。
