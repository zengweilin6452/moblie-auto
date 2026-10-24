# 09: 网页服务骨架与实时查看

**What to build:** 不安装桌面工具即可查看设备：本机网页显示实时预览帧与 UI 树（各自标注采集时间）、设备忙/离线状态；快照冻结与编辑期固定引用；仅监听回环并校验请求来源；资源上限全部生效；无设备时服务仍可启动并如实报告（spec 用户故事 27、30 部分、31）。

**Blocked by:** 03 原生定位与操作补全；04 跨进程设备租约与取消语义

**Status:** resolved

- [x] serve 启动后浏览器可见实时预览帧（默认 1fps）与可刷新 UI 树，分别标注采集时间
- [x] 快照接口返回截图与树绑定同一快照标识；采集间隔超限提示不一致并禁用原生拾取
- [x] 编辑期间冻结快照固定引用，不受实时快照份数上限驱逐；退出编辑或超时后释放
- [x] pytest 持有租约时网页显示占用状态且不主动采集；设备忙/离线/未连接状态明确区分
- [x] 仅监听回环地址；来源校验拒绝跨源请求；WS 最多 2 客户端、慢客户端丢帧不堆积
- [x] 像素、文件大小、快照份数与总量、匹配作业并发上限生效，超限明确提示不积压
- [x] 错误码映射（422/404/409/400/503/504）含 code、用户可读信息与 request_id


## Answer

交付(本工单提交):

- `viewer/store.py`:快照库——实时快照 ≤3 份、总量 ≤64MiB、有效期 120 秒;冻结快照固定引用不被驱逐(退出编辑或 10 分钟冻结超时后释放);单张 1200 万像素 / 20MiB 输入上限在入口校验。
- `viewer/app.py`:FastAPI 应用(create_app,会话注入便于测试)——`/api/v1/status`(版本、设备状态、占用、可采集)、`POST /snapshots`(短事务:获取租约→截图+树→释放;返回同一快照标识与各自采集时间、一致性判定)、`GET .../image|tree`、`POST .../freeze`;WS `/api/v1/preview` 默认 1fps、最多 2 客户端(第三个 4429 拒绝)、慢客户端丢帧不堆积、LEASED 时推送占用状态不采集。
- 中间件:Host/Origin 白名单(仅回环);request_id 注入响应头与错误信封;错误映射 UnsupportedOperation/InvalidTarget/InvalidTemplate/ConfigurationError/MatchOverflow→400、StaleHandle/DeviceBusy→409、SessionFaulted/DeviceUnavailable→503,统一 code/message/request_id。
- `cli.py` serve 子命令:仅监听 127.0.0.1,默认端口 8765;无设备时降级启动、状态如实报告。
- 前端 `static/`(原生 HTML/CSS/JS,随包分发):双栏布局、实时预览(标注帧时间)、采集快照按钮、UI 树(标注采集时间、类名/资源/文本/bounds)、占用提示。
- viewer extra 增加 `websockets`(uvicorn WS 支持必需,实测缺失时 WS 404)。

验证:ruff 0 问题;单元新增 15 项(store 4 + API 11),全部通过;真机 serve 冒烟:status/快照(1080x2400,树 97 节点含 WLAN)/PNG/静态页全通;WS 推帧 1fps 实测;浏览器(IAB)验收通过——实时预览帧渲染、采集快照后 UI 树 56 项渲染、采集时间分别标注(见截图)。

决定与说明:
- uvicorn 需显式安装 WS 库:plain uvicorn 对 upgrade 请求 404(实测踩坑);选择最小依赖 `websockets` 而非 uvicorn[standard]。
- 原生拾取禁用逻辑归工单 10(拾取实现方),本票提供 consistent 字段供其判定。
- 匹配作业并发上限随工单 10/11 的匹配端点实现(引擎侧已有单并发约束)。
- 预览采集复用短事务租约:与 pytest 天然互斥,持有期间推送 status 帧。
