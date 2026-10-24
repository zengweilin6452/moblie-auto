# 03: 原生定位与操作补全

**What to build:** 原生能力全量可用:XPath、可访问性描述、u2 选择器(JSON 白名单字段)三种定位描述补全,index 与歧义规则;输入为清空替换且拒绝不可编辑目标;属性读取区分未知与缺失;返回/按键/滚动有确定语义;goto 受 Deep Link 白名单约束;wait_for_load 仅等待启动就绪目标;is_visible/find_elements/close/错误分类齐全;原生查询句柄重用校验;UI 树获取与归一化(spec 用户故事 4–12)。

**Blocked by:** 02 设备会话与第一条真机用例

**Status:** resolved

- [x] 五种原生定位描述全部可用;u2 选择器拒绝未知字段、代码与正则;index 拒绝 bool、负数与字符串数字
- [x] find_elements 无序号返回全部可见匹配、有序号返回至多一个,顺序为树遍历序
- [x] input_text 清空并替换;不可编辑目标明确失败,不以全局输入冒充
- [x] get_attribute:布尔以 true/false 字符串返回、bounds 为固定四整数、未知属性报错、已知缺失返回空串
- [x] scroll 方向语义(down = 内容上移)、amount 夹限到安全区域;press_key 有限键表、未知键报错
- [x] goto:空串启动配置 App;非空仅接受白名单 scheme,未配置或非法报配置错误,不执行任意 shell
- [x] wait_for_load 仅等待启动就绪目标,缺配置报错,不以固定 sleep 替代
- [x] close 幂等;异常分类可区分非法定位描述、定位超时、匹配歧义、设备不可用、不支持、取消
- [x] 原生句柄重用:关键属性不一致或会话变化报句柄过期,不指向新节点
- [x] UI 树归一化(节点、父子关系、bounds、属性)产出可供失败证据与网页复用
- [x] 真机操作矩阵(正常/失败/边界)在演示 App 上通过;CSS 定位描述明确报不支持

## Answer

交付(本工单提交):

- `selectors.py`:五种原生定位(NATIVE_LOCATORS);`parse_u2_selector` JSON 白名单(5 字符串字段 + 4 布尔字段,textContains/matches 等模糊与正则字段拒绝);`attribute_value`/`validate_attribute_name`(布尔→"true"/"false",bounds→四整数,已知缺失→空串,未知→InvalidTarget);`ensure_editable`(className 含 EditText 判定,u2 info 无 editable 字段);`scroll_points`(down=手指向上,amount 夹限到 10% 安全边距);`revalidate_handle`(句柄重定位 + 关键属性校验)。
- `contracts.py`:ElementHandle 扩充身份字段(class_name/resource_id/content_desc,文本不参与身份)与操作方法 click/get_text/input_text/get_attribute,经绑定会话执行,重用前自动重校验。
- `backend_u2.py`:xpath 走 u2 独立 XPath 子系统(d(xpath=...) 不被支持,实测确认);description/u2 选择器映射;元素 clear_text/set_text;open_deeplink(参数化 `am start -a VIEW -d uri`,list 传参不拼 shell);press_key 有限键表(delete→del 别名);swipe;dump_ui。
- `adapter.py`:input_text(清空替换,预算内轮询)、get_attribute、back/press_key、scroll、wait_for_load(缺配置报 ConfigurationError,超时抛 WaitTimeout)、goto 白名单内 Deep Link 经 open_deeplink 交付。
- `ui_tree.py`(新文件,plan §10 文件表之外的合理补充):UiNode 归一化树、parse_ui_hierarchy、flatten 深遍历。
- 设备测试:操作矩阵(五定位/歧义/不可编辑/属性/输入/滚动/句柄过期/home 键)与 UI 树 dump;local.toml 增加 launch_ready_target。

验证:ruff 0 问题;单元 115 项通过;真机 4 项通过(操作矩阵 + UI 树 + 首流程 + 会话)。

决定与说明:

- 可编辑性以 className 含 "EditText" 判定:u2 info 不含 editable 字段,以最常见输入框基类近似;自绘输入控件(非 EditText 类)会被拒绝,登记为已知限制。
- 原生句柄身份 = className + resourceId + contentDescription(文本易变不参与):主/子页标题同 id 的节点身份相同,属该规则的已知盲区;位置校验仅 IMAGE 句柄要求(plan 6.2),设备测试改用"子页元素消失"路径演示句柄过期。
- scroll 的安全边距取屏幕尺寸 10%(校准初值);手势时长 0.3 秒(plan 5.3)。
- press_key 未知键报 InvalidTarget(与不支持定位方式同类:参数取值越界)。
- CSS 定位:Locator 枚举不含 CSS,Target(by="css") 构造即报 InvalidTarget(合同层拒绝,有测试)。
- u2 XPath 语法与标准 Xml 不同:标签名为类名,`//node[...]` 匹配不到,须用 `//*[@attr="v"]`;设备测试使用后者。
