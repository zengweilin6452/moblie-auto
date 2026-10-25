"""底部导航往返:首页 / 课程 / 我的 + 动态第三端点。

实测:底部导航第三端点由服务端动态配置——2026-10-25 当日由「直播中心」
变为「知识库」;用例对稳定端点(首页/课程/我的)固定断言,第三端点
按候选集动态发现。TEXT 锚点受语言漂移影响(中↔英),失败时先核对
页面语言,长期方案为 IMAGE 模板或补 desc。
"""

from __future__ import annotations

import pytest
from flows import (
    MINE_STUDY,
    TAB_COURSE,
    TAB_HOME,
    TAB_LIVE,
    TAB_MINE,
    goto_home,
    open_tab,
)

from mobile_auto import Locator, Target

pytestmark = pytest.mark.platform("android")

# 第三端点候选(服务端动态配置,实测两态)
DYNAMIC_TABS = (TAB_LIVE, Target(Locator.TEXT, "知识库"))


@pytest.fixture(autouse=True)
async def _at_home(app):
    landing = await goto_home(app)
    if landing == "login":
        pytest.skip("会话过期落在登录页:人工登录后重跑")


async def test_mine_tab_shows_profile_stats(app):
    assert await open_tab(app, TAB_MINE, anchor=MINE_STUDY), "「我的」缺学习统计区块"


async def test_course_and_dynamic_tabs_reachable(app):
    assert await open_tab(app, TAB_COURSE), "「课程」页签不可见"
    for tab in DYNAMIC_TABS:
        if await app.is_visible(tab):
            assert await open_tab(app, tab), f"动态端点 {tab.value} 不可达"
            break
    else:
        pytest.fail(f"底部导航第三端点缺失(候选:{[t.value for t in DYNAMIC_TABS]})")


async def test_tab_roundtrip_returns_home(app):
    await open_tab(app, TAB_MINE)
    assert await open_tab(app, TAB_HOME, anchor=None)
    from flows import HOME_SEARCH

    assert await app.wait_for(HOME_SEARCH, timeout=5), "回首页后缺 desc=Search"
