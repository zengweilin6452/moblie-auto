# 演示 App 登记(spec 决定 Q4)

真机操作类证据使用固定来源的现成演示 App。本文件登记其来源、版本、摘要与场景清单;
换机或升级后必须重新登记,不得沿用过期摘要。

## 登记(2026-10-24,工单 02)

| 项 | 值 |
| :--- | :--- |
| App | Android 系统设置(com.android.settings) |
| 来源 | 测试机系统预装应用,从设备拉取原包登记,不做修改 |
| 测试机 | Xiaomi 2206123SC,Android 14(SDK 34),USB 序列号 749622c9 |
| versionName / versionCode | 14 / 34 |
| APK 路径(设备端) | /system_ext/priv-app/Settings/Settings.apk |
| APK sha256 | `f34f979200023fd5236a0794c88d7b3b11b64dd24f951c9a1df6420f0b7c968c` |
| 大小 | 105,114,528 字节 |
| 登记方式 | `adb pull` 原包后计算摘要;设备系统升级后须重新拉取登记 |

选择理由:系统应用随设备可用、无账号与隐私数据、resource-id 与文本稳定,
适合作为原生定位/操作的首个真机载体。它不替代后续工单需要的"现成演示 App"
完整场景覆盖;若阶段 1 后续需要更深场景(列表滚动、输入),另选应用并重新登记。

## 场景清单(工单 02 已覆盖)

前置:设备已授权 USB 调试并在线;配置 `tests/device/local.toml` 指定
`app_package = "com.android.settings"`、`app_activity = ".MainSettings"`
(显式启动主 activity 会复位到设置主页,保证用例起点确定)。
锁屏假设为无密码滑动锁:测试夹具会点亮屏幕并上滑解锁;
若设备设置了密码,请先手动解锁,否则用例失败属环境问题。

| 场景 | 步骤 | 断言 |
| :--- | :--- | :--- |
| 启动与主页就绪 | goto("") 冷启动设置(先停止再启动) | wait_for(text="WLAN") 返回 True |
| 定位与点击 | click(text="WLAN") 进入 WLAN 子页 | wait_for(text="高级设置") 返回 True |
| 读取文本 | get_text(text="高级设置") | 文本等于 "高级设置" |
| 枚举与句柄 | find_elements(text="WLAN") | 至少 1 个句柄,携带 bounds |
| 截图 | screenshot() | 返回 PNG 魔数开头的全新字节 |
| 五种原生定位(工单 03) | text/u2_selector/xpath/accessibility_id/resource_id(+index) | 各定位均可见或返回句柄;resource_id 多行同 id 无序号报歧义 |
| 输入(工单 03) | click(desc="搜索") → input_text(android:id/input) | get_text 读回一致,替换而非追加;非输入框拒绝 |
| 属性(工单 03) | get_attribute(enabled/bounds/contentDescription) | 布尔为 "true"/"false";bounds 为四整数;缺失返回空串 |
| 滚动(工单 03) | scroll("down", 1200) | 列表底部项(text="桌面")可见 |
| 坐标手势(2026-10-25) | adapter.device.tap/swipe(auto→scrcpy) | tap 进 WLAN 子页;swipe 滚到底部("桌面"可见)同契约 scroll 判据 |
| 长按(2026-10-25) | adapter.device.long_press(MIUI 桌面空白处,0.8s) | 双通道(scrcpy/u2)均进入桌面编辑模式(「小部件」标记) |
| 句柄过期(工单 03) | 子页取"高级设置"句柄后返回主页再重用 | 报 StaleHandle,不指向新节点 |
| UI 树(工单 03) | backend.dump_ui() + parse_ui_hierarchy | 归一化树含设置页面节点 |

冷启动保证用例从设置主页顶部开始(设置进程存活时列表会保留滚动位置,
曾导致 WLAN 不在可见区)。搜索流会残留结果文本,需要干净主页的断言
须在冷启动后、搜索流之前执行(见 test_operations_matrix.py 顺序)。
用例结束停留在 WLAN 子页,不修改任何设置项;
如需还原,手动返回桌面即可。首次建立会话时,u2 会向设备推送其自动化
组件(atx-agent / app-uiautomator),这是显式运行真机用例的一部分,
doctor 不执行该初始化。

## 已知设备特性

- 小米 ROM 上 `dump_hierarchy` 偶发返回空结果(u2 内部重试后可恢复);
  若真机用例出现偶发空树失败,先重跑一次再归因。
- 设备空闲会息屏:测试夹具负责点亮屏幕并上滑解锁(假定无密码锁,
  设了密码请先手动解锁);夹具唤醒属测试环境准备,不属于驱动契约。
- 跑 Beta_Academy 等联网 App 前 `svc power stayon usb`,结束后
  `svc power stayon false`(息屏时该 App dump 返回空 XML、截图为黑图,
  静默降级不报错)。

## 深场景探查对象:Beta_Academy(2026-10-25,非演示 App)

Beta_Academy V1.9.77(com.sinosoft.trainapp,.MainActivity)为在用的
真实培训 App(带账号数据与水印),**不登记为演示 App**(演示 App 需
无账号与隐私数据),仅作为深场景(列表滚动/嵌套滚动/WebView 等)的
探查对象;换版本后结论需复核。验证脚本与证据在 docs/snapshots/
(verify_coldstart_recovery.py、verify_deep_scenarios.py、deep_*.xml/png)。

- 冷启动:会话有效时落点 100% 稳定为首页(goto+wait_for_load
  (desc='language') 约 1.3s);登录页落点 = 会话过期,重启不可恢复,
  需人工登录(图形验证码)。分类判据与加固见 docs/validation.md
  Beta_Academy 章节。
- 语言漂移(中↔英,服务端下发)仍存在:TEXT 定位不可靠,优先
  accessibility desc(Search/language/scan/settings 均语言无关)。
- 导航中随机弹 MIUI 系统权限对话框(com.lbe.security.miui,媒体/通知):
  自动化流须带对话框清理(脚本统一点「拒绝」保持既有授权状态)。
- 深场景可行性结论(详情见 docs/validation.md):
  列表滚动加载部分可行(数据量不足以触发分页);嵌套滚动结构存在但
  契约 scroll 无法定向内层;WebView 可行(设置→问题反馈);长按、
  多窗口/分屏当前契约不可行(缺坐标手势/长按/分屏触发 API)。
- 首页结构:外层 ScrollView [0,0][1080,2126] + 内嵌横向容器
  ([0,0][1080,637]、[0,983][1080,1170]);课程页仅 3 张卡(0节);
  知识库列表 5 项(「数量:N」字段可作条目标记)。App 为 Compose 实现,
  无 resource-id;settings 页有 修改密码/问题反馈/指纹登录/退出登录。
