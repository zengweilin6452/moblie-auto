"""BYD Academy(Beta_Academy)移动端示例套具:pytest fixture 注入平台信息。

平台注入三件套(与内网 browser-harness 双轨对接同形):
1. 归属声明:@pytest.mark.platform("android") / ("web");可叠加多标记=多端通用。
2. 运行期选择:--platform 旗标 + collection 钩子,非当前端的用例整体跳过
   (显示 skipped,不计为通过);不指定旗标时执行全部。
3. fixture 分发:
   - platform(function):注入当前用例的端身份(旗标 > 用例标记 > 默认 android);
   - app(function):按端注入驱动——android 分支给 MobileAdapter,web 分支由
     内网绑定层注入 Browser(本示例环境无 web 驱动,明确跳过)。
   用例只面向注入的对象写业务断言,不感知端差异。

环境事实(2026-10-25 真机验证,docs/validation.md):
- 息屏时 dump 返回空、截图黑屏不报错 → 会话级亮屏 + USB 常亮;
- MIUI 系统权限对话框随机弹出挡住锚点等待 → 导航助手统一清理(点「拒绝」
  保持既有授权状态);
- 页面语言中英漂移(服务端下发)→ 优先 accessibility desc 锚点(语言无关),
  TEXT 锚点处已标注风险;
- App 为 Compose 实现,无 resource-id。
"""

from __future__ import annotations

import pytest

from mobile_auto import DeviceSession, MobileAdapter, load_config


def pytest_addoption(parser):
    parser.addoption(
        "--mobile-config",
        default=None,
        help="移动端配置 TOML(复制 local.toml.example 为 local.toml)",
    )
    parser.addoption(
        "--platform",
        default=None,
        metavar="{android,web}",
        help="当前执行端;非该端的用例整体跳过(缺省=执行全部)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "platform(name): 用例归属端(android/web);可多标记"
    )


def _mark_platform(mark) -> str:
    """读取 platform 标记的端名:兼容位置参数 platform("web") 与具名
    platform(name="web") 两种写法。"""
    if mark.args:
        return str(mark.args[0])
    return str(mark.kwargs.get("name", "android"))


def pytest_collection_modifyitems(config, items):
    active = config.getoption("--platform")
    if not active:
        return
    skipped = pytest.mark.skip(reason=f"非当前端(--platform={active})")
    for item in items:
        platforms = {_mark_platform(m) for m in item.iter_markers(name="platform")}
        if platforms and active not in platforms:
            item.add_marker(skipped)


# ── 平台身份与驱动注入 ───────────────────────────────────


@pytest.fixture
def platform(request) -> str:
    """注入到用例的端身份:--platform 旗标 > 用例 platform 标记 > android。

    会话级配置(快照命名、报告维度)读旗标;用例内分支判断读本 fixture。
    """
    flag = request.config.getoption("--platform")
    if flag:
        return flag
    for mark in request.node.iter_markers(name="platform"):
        return _mark_platform(mark)
    return "android"


@pytest.fixture
def app(platform, mobile_session):
    """端驱动分发:android → MobileAdapter;web → 内网绑定层注入 Browser。

    本示例环境只有 Android 真机;web 分支明确跳过——内网接入时由绑定层
    用真实 Browser 实现同名 fixture,双端通用用例零改动。
    """
    if platform == "web":
        pytest.skip("web 端驱动由内网 browser-harness 注入;单端示例环境跳过")
    return MobileAdapter(mobile_session)


# ── Android 会话与环境看护(仅 android 端需要)──────────


@pytest.fixture(scope="session")
def mobile_config(request):
    path = request.config.getoption("--mobile-config")
    if not path:
        pytest.skip(
            "未提供 --mobile-config(复制 examples/byd_academy/local.toml.example"
            " 为 local.toml 后指定)"
        )
    return load_config(path)


@pytest.fixture(scope="session")
def mobile_session(mobile_config):
    session = DeviceSession(mobile_config)
    session.start()
    yield session
    session.close()


@pytest.fixture(scope="session", autouse=True)
def _keep_screen_on(mobile_config):
    """亮屏 + USB 供电期间常亮:息屏会让 dump 空返回/截图黑图(实测)。"""
    import adbutils

    device = adbutils.adb.device(serial=mobile_config.device_serial)
    _wake_screen(device)
    device.shell("svc power stayon usb")
    yield
    device.shell("svc power stayon false")


def _wake_screen(device) -> None:
    """点亮并上滑解锁(假定无密码锁屏;与 tests/device 同一前置假设)。"""
    import uiautomator2 as u2

    d = u2.connect(device.serial)
    if not d.info.get("screenOn"):
        d.screen_on()
        d.swipe(0.5, 0.9, 0.5, 0.2, duration=0.2)
