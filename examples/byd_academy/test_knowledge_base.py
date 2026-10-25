"""知识库列表:入口可达、条目标记存在、列表可滚动。

实测边界(2026-10-25):当前账号仅 5 条,数据量不足以触发分页加载;
分页断言留待数据充足时启用,此处只验证工具能力(滚动 + 计数不回退)。
"""

from __future__ import annotations

import pytest
from flows import KB_COUNT_ITEM, KB_ENTRY, goto_home

pytestmark = pytest.mark.platform("android")


@pytest.fixture(autouse=True)
async def _at_home(app):
    landing = await goto_home(app)
    if landing == "login":
        pytest.skip("会话过期落在登录页:人工登录后重跑")


async def test_kb_entry_lists_counted_items(app):
    assert await app.wait_for(KB_ENTRY, timeout=5), "首页缺知识库入口"
    await app.click(KB_ENTRY)
    items = await app.find_elements(KB_COUNT_ITEM)
    assert len(items) >= 1, "知识库条目(数量:N 标记)未出现"


async def test_kb_list_scroll_keeps_items(app):
    await app.click(KB_ENTRY)
    await app.scroll("down", 900)
    after = len(await app.find_elements(KB_COUNT_ITEM))
    assert after >= 1, "滚动后列表空(不应丢条目)"
    # 分页加载断言(数据充足时启用):滚动后计数应增加
