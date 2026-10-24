# 01: 包骨架与 doctor 环境自检

**What to build:** 测试工具开发者可以安装本包并用一条命令确认环境可用:包可编辑安装,CLI 提供 doctor 子命令,报告依赖版本、ADB 可见性与已授权设备在线状态;缺依赖时非零退出且不修改设备。doctor 仅执行无副作用检查,设备端组件初始化是独立显式步骤(spec 用户故事 1、2 的环境部分)。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] 可编辑安装成功;公开导出可导入且导入无设备副作用
- [x] doctor 报告依赖版本与 ADB 可见性;缺依赖非零退出并列出缺失项
- [x] doctor 区分已授权在线设备 / 未授权 / 无设备,不自动选择设备
- [x] doctor 不触发设备端组件初始化、不安装、不写入设备
- [x] 配置合并生效:显式参数 > 环境变量 > TOML > 默认值;非法值报配置错误
- [x] lint 与单元测试在干净检出上可运行

## Answer

交付(提交 ceb6682 + 评审修复提交):

- `pyproject.toml`:包名 mobile-auto,Python >=3.12,核心依赖 uiautomator2/Pillow,extras(image/viewer/dev),console script `mobile-auto`,pytest/ruff 配置,device marker。
- `src/mobile_auto/errors.py`:plan 5.4 全部 13 类错误 + MobileAutoError 基类。
- `src/mobile_auto/contracts.py`:Locator(6 种定位方式,CSS 不支持)、Target(冻结,校验 index/confidence/region)。
- `src/mobile_auto/config.py`:MobileConfig 与 load_config,合并优先级 显式参数 > 环境变量 > TOML > 默认值;assets/artifacts 相对路径按配置文件目录(无配置按 cwd)解析为绝对路径。
- `src/mobile_auto/cli.py`:`mobile-auto doctor`(serve 属 ticket 09),报告 Python/依赖版本、ADB 路径、设备按 已授权/未授权/离线/其他 分组;注入式检查便于测试。
- `tests/unit/`:50 个测试(contracts 校验、配置合并与非法值、doctor 行为、导入无副作用)。

验证:可编辑安装(`pip install -e ".[dev]"`)成功;`ruff check` 0 问题;`pytest tests` 50 通过;真实运行 `mobile-auto doctor`:发现 adbutils 自带 adb.exe、列出已授权在线设备 749622c9,退出码 0;坏配置退出码 2 并报字段名;`python -m mobile_auto.cli doctor` 可用。

决定与说明:

- 退出码:0=环境可用(含仅有警告),1=缺核心依赖或 ADB 不可用/探测失败,2=配置错误。无设备、仅有未授权设备、配置序列号不在线均为警告,不失败。
- 可选 extra(image/viewer)缺失只警告;核心依赖缺失才非零退出。
- ADB 发现顺序:adbutils 内置二进制(随 uiautomator2 安装,本机验证存在)→ PATH。未新增环境变量或配置键。用户本机 platform-tools(D:\workspace\zcodetest\app-performance-auto\platform-tools)在后续需要真机的 ticket 中按需复制到项目根(已在 .gitignore 排除)。
- `adb devices` 可能启动本机 adb 服务,属主机侧行为(检查设备在线状态所必需),已在 doctor 输出中注明;doctor 对设备的唯一动作是只读的 `adb devices`,有测试锁定该命令面。
- 公开导出目前为 errors×13 + Locator/Target + MobileConfig/load_config;MobileAdapter/DeviceSession/ElementHandle 随 ticket 02/03 交付并扩充 `__all__`。
- 有意超出最低要求:TOML 未知键报 ConfigurationError(防拼写静默失效);AdbProbeError 为 doctor 内部探测错误,不属 plan 5.4 驱动错误类别,不逸出 doctor。
- code-review(Standards/Spec 双轴)发现的测试引用私有成员、无用参数、过宽异常捕获、默认目录相对路径歧义等已全部修复;serve 缺失与 requirements-lock.txt 属后续 ticket 范围,非遗漏。
