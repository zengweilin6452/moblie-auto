"""冷启动落点分类:落点由服务端会话状态决定,不是随机竞态(实测 15/15)。

分类判据(语言无关):
- 首页 = desc=Search(登录页没有);
- 登录页 = 多个 EditText(账号/密码/验证码)且无 desc=Search。
注意首页搜索框也是 EditText,不能以 EditText 存在性分类。
"""

from __future__ import annotations

import pytest
from flows import HOME_LANGUAGE, HOME_SEARCH, LOGIN_EDITTEXT, goto_home

pytestmark = pytest.mark.platform("android")


async def test_coldstart_lands_on_home_when_session_valid(app):
    landing = await goto_home(app)
    if landing == "login":
        pytest.skip(
            "会话过期落在登录页:图形验证码不可自动化,须人工登录一次后重跑"
        )
    assert await app.wait_for(HOME_SEARCH, timeout=5), "首页缺 desc=Search 锚点"
    assert await app.wait_for(HOME_LANGUAGE, timeout=5), "首页缺 desc=language 锚点"


async def test_login_landing_is_classifiable_not_error(app):
    """落在登录页时必须能被分类(而非误报为超时/异常):登录页判据成立。"""
    landing = await goto_home(app)
    if landing == "home":
        pytest.skip("当前会话有效,直达首页;登录页判据见 coldstart_recovery 记录")
    inputs = await app.find_elements(LOGIN_EDITTEXT)
    assert len(inputs) >= 2, f"登录页应含多个输入框,实际 {len(inputs)}"
    assert not await app.is_visible(HOME_SEARCH), "登录页不应出现 desc=Search"
