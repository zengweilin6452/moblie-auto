"""设置 → 问题反馈:App 内唯一 WebView 入口(实测确认)。

路径:「我的」→ 齿轮(desc=settings,语言无关)→ 问题反馈(TEXT)。
WebView 容器出现即视为到达;容器内 DOM 不在本包自动化范围
(WebView 内部 DOM 自动化属首版排除项,见 PRD §2.2)。
"""

from __future__ import annotations

import pytest
from flows import (
    MINE_SETTINGS,
    SETTINGS_FEEDBACK,
    TAB_MINE,
    WEBVIEW_NODE,
    goto_home,
    open_tab,
)

pytestmark = pytest.mark.platform("android")


async def test_feedback_opens_webview(app):
    landing = await goto_home(app)
    if landing == "login":
        pytest.skip("会话过期落在登录页:人工登录后重跑")

    assert await open_tab(app, TAB_MINE), "「我的」页签不可见"
    assert await app.wait_for(MINE_SETTINGS, timeout=5), "「我的」缺设置齿轮(desc)"
    await app.click(MINE_SETTINGS)
    assert await app.wait_for(SETTINGS_FEEDBACK, timeout=6), "设置页缺问题反馈入口"
    await app.click(SETTINGS_FEEDBACK)

    webviews = await app.find_elements(WEBVIEW_NODE)
    assert len(webviews) >= 1, "问题反馈应打开 android.webkit.WebView 容器"

    await app.back()  # 归位,减少对后续用例的状态影响
