# 08: IMAGE 驱动操作

**What to build:** UI 树覆盖不了的视觉目标也能自动化：IMAGE 定位描述驱动 click、is_visible、wait_for、find_elements——每次新采集截图匹配、点击为完整设备事务；图像查询句柄带身份校验，页面重排后报句柄过期而非点错目标；不支持的操作组合明确报错（spec 用户故事 13、16、17）。

**Blocked by:** 02 设备会话与第一条真机用例；06 图像匹配引擎；07 模板资产库

**Status:** resolved

- [x] 真机用例：用模板点击演示 App 的视觉目标，后置业务断言通过
- [x] click 单目标要求唯一匹配或显式序号；多目标无序号报歧义，不自动取最高分
- [x] 采集完成到点击超时则重新采集一次，再超时失败，不无限重试；事务内无其他设备请求插队
- [x] 图像句柄重用校验模板摘要、会话、尺寸方向与位置一致性；身份不明报句柄过期（含列表插入/删除重排场景）
- [x] input_text、get_text、get_attribute、extract_list 对 IMAGE 明确报不支持（各有测试）
- [x] 匹配无结果是明确的查询结果（空列表/False），非法模板与设备错误分开报告
- [x] 模拟设备验证取消窗口贯通：发送阶段检查点在 IMAGE 路径同样生效


## Answer

交付(本工单提交):

- `adapter.py`:IMAGE 分支——`_image_match`(资产库加载模板 + 新采集截图 + 引擎匹配)、`find_elements/is_visible/wait_for` 支持单轮采集匹配(无结果 = 空列表/False,是查询结果);`click` 走 `_image_click_transaction` 单队列事务(采集→匹配→选择→检查点→tap,其他请求无法插队),采集到发送超 1 秒重采集一次、仍过期报 DeviceUnavailable 不无限重试;`input_text/get_text/get_attribute` 对 IMAGE 显式 UnsupportedOperation(extract_list 属内网 schema 门槛,本就未实现,见工单说明)。
- `selectors.py`:`make_image_handle`(模板摘要、原边界、屏幕尺寸与方向)、`revalidate_image_handle`(模板摘要→屏幕尺寸→方向→重新匹配→IoU≥0.5 位置一致性,任一失守报 StaleHandle)、ImageNode(点击落到结果中心)。
- `contracts.py`:ElementHandle 增加图像字段;句柄操作按定位方式分派原生/图像重校验;图像句柄仅支持 click,文本类操作显式拒绝。
- `backend_u2.py`:DeviceBackend 增加 tap(u2 click)。
- 设备测试 `test_image_flow.py`:实时截图按原生句柄 bounds 裁剪视觉目标(WLAN 行含彩色图标)→ 资产库 create-or-update → IMAGE 查询匹配位置一致 → 图像点击 → 后置业务断言(WLAN 子页就绪)。
- 单元测试 15 项:计数查询、序号与歧义(不自动取最高分)、区域限制、文本矩阵拒绝、非法模板/设备错误分离、句柄四类过期(稳定复用/布局移位/模板更新/尺寸变化)、取消窗口贯通。

验证:ruff 0 问题;单元 194 项通过;真机 7 项(xfail 1 + 通过 6,含 IMAGE 流程)通过。

决定与说明:
- IMAGE click 不做预算轮询:单事务语义,无匹配即 ElementNotFound(plan 6.2 只规定过期重采,不规定等待);需要等待用 wait_for。
- 图像句柄的"会话"校验即 owner 会话绑定(工单 04 机制);session generation 随绑定层接入细化,此处以 owner 身份与会话状态为准。
- 采集新鲜度时钟用 time.monotonic;过期错误归入 DeviceUnavailable(设备响应不足以支撑图像事务),错误类别映射可在绑定期调整。
- 真机测试模板按逻辑名 create-or-update,避免重复运行报同名冲突。
