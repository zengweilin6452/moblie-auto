"""平台注入演示:用例如何声明端、运行期如何区分与分发驱动。

- @pytest.mark.platform(...):归属声明(可多标记=多端通用);
- --platform 旗标:非本端用例整体跳过(collection 钩子,见 conftest);
- platform / app fixture:端身份与端驱动注入,用例不感知差异。

试跑:
    pytest examples/byd_academy --mobile-config examples/byd_academy/local.toml
    pytest ... --platform=web      # 观察非本端用例整体跳过
"""

from __future__ import annotations

import pytest

# 注意:本文件不用模块级 pytestmark 统一打标——端归属是逐用例声明的
# (模块级标记会叠加进本文件内其他端的用例,platform fixture 取首标记)。


@pytest.mark.platform("android")
async def test_platform_identity_injected(platform, app):
    """注入的端身份与驱动自报身份一致(端信息的两条来路互证)。"""
    assert platform == "android"
    assert app.platform == "android"  # 驱动侧固定属性


@pytest.mark.platform("android")
async def test_android_driver_works(app):
    """android 分支注入的驱动可用:截图为有效 PNG。"""
    png = await app.screenshot()
    assert png[:4] == b"\x89PNG" and len(png) > 10_000


@pytest.mark.platform("web")
async def test_web_only_case_needs_browser(platform, app):
    """web 专属用例:本示例环境 app 在 web 分支明确跳过;

    内网接入时绑定层把 Browser 注入同名 app fixture,本用例零改动。
    """
    assert platform == "web"
    assert await app.title_contains("BYD")  # web 驱动面(示意)


@pytest.mark.platform("android")
@pytest.mark.platform("web")
async def test_shared_case_runs_on_both_ends(platform, app):
    """双端通用用例:同一业务断言,端差异(驱动/交互方式)由 fixture 吸收。"""
    if platform == "android":
        png = await app.screenshot()
        assert png[:4] == b"\x89PNG"
    else:
        assert await app.screenshot()  # web 驱动同形接口(绑定层保证)
