# 11: 图像模板制作闭环

**What to build:** 无 UI 树的视觉目标从制作到点击全闭环：浏览器框选画面，服务端从保留快照裁剪原始像素生成模板并存入资产库（元数据自动登记），设置阈值与搜索区域，重新匹配显示分数与高亮，复制 IMAGE 定位描述表达式，粘贴进用例真机点击通过（spec 用户故事 18、29 的 IMAGE 部分）。

**Blocked by:** 06 图像匹配引擎；07 模板资产库；09 网页服务骨架与实时查看

**Status:** resolved

- [ ] 框选生成模板预览；保存走资产库版本化流程，默认拒绝同名，覆盖需确认旧摘要
- [ ] 裁剪使用服务端保留的原始截图，不接受浏览器重压缩图片冒充原图
- [ ] 可设置匹配阈值与搜索区域；验证在新截图上重匹配，显示数量、分数与边界框
- [ ] 多目标展示稳定序号供确认；零匹配不显示为通过
- [ ] 复制的 IMAGE 定位描述（相对模板路径、阈值、搜索区域、可选序号）粘贴进示例用例真机通过
- [ ] 模板资产迁移到新环境后用例不失效（相对路径验证）


## Answer

交付(本工单提交):

- `viewer/app.py` 模板端点:`POST /api/v1/templates`(服务端从保留快照裁剪原始像素,region 校验,同名 InvalidTemplate→422);`PUT /api/v1/templates/{logical}`(期望旧摘要确认更新,不符 409 冲突);`GET /api/v1/templates`(清单);`GET .../image`(模板预览 PNG);`POST /api/v1/templates/validate`(重新采集匹配,回传数量/分数/边界框;非 IMAGE 目标 422;零匹配不显示为通过)。
- 裁剪实现 `_crop_and_register`:decode_rgb(拒绝透明/损坏)→ 越界拒绝 → 原像素裁剪 → 资产库 create/update(版本化+摘要链),从不接受浏览器上传图片。
- 前端图像标签:画面拖动框选(marquee 虚线框,阈值 4px 防误点)、模板名/阈值输入、保存(同名时自动取旧摘要走确认更新,confirm 弹窗)、重新匹配验证(红框标注命中+分数)、复制 IMAGE 定位表达式(含 confidence/region)。
- 修复:`_query_payload` 对 MatchResult 与后端元素的 bounds 语义统一(此前 MatchResult 被按 (l,t,r,b) 处理,出现负高度——真机 e2e 抓出);验证错误映射 InvalidTarget/InvalidTemplate→422(输入校验语义)。
- 测试:单元 6 项(裁剪制作/同名冲突与确认更新/越界 422 与快照 404/真引擎验证带分数/非 IMAGE 422);真机 e2e(test_template_studio_e2e.py):冷启动 → 快照裁剪整行制作模板 → 验证(count≥1)→ 表达式 Target 经适配器 IMAGE 点击 → WLAN 子页业务断言。

验证:ruff 0 问题;单元 228 项通过;真机 9 项通过(含 e2e 点击与业务断言)。

决定与说明:
- 真机 e2e 以唯一时间戳逻辑名新建:资产库跨运行持久,同名走更新分支会掩盖"新建"路径;单元测试覆盖更新路径。
- 小米偶发空 dump 在 e2e 中按登记特性重试采集(docs/demo-app.md)。
- unit/device 测试文件同名会引发 pytest 收集冲突,device 侧文件加 _e2e 后缀。
- 模板资产相对路径迁移验证归入工单 12 的干净环境安装(wheel 内不带用户模板,迁移语义由 assets_dir 相对配置保证,已在工单 07 实现)。
