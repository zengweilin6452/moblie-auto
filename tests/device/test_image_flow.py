"""真机 IMAGE 流程(工单 08):模板制作 → 图像点击 → 后置业务断言。

视觉目标取自主页 WLAN 行(含彩色图标与文本),从实时截图裁剪生成模板,
经资产库登记后用 IMAGE 定位点击;断言以 WLAN 子页文本就绪为准。
"""

import cv2
import pytest

from mobile_auto import Locator, Target
from mobile_auto.imaging.engine import decode_rgb
from mobile_auto.imaging.store import TemplateStore


def png_bytes(arr_rgb) -> bytes:
    ok, buf = cv2.imencode(".png", arr_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


@pytest.mark.device
async def test_image_template_click_business_flow(adapter, mobile_config):
    await adapter.goto("")
    wlan_row = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(wlan_row) is True

    # 模板制作:操作时截图 + 原生句柄 bounds 定位裁剪区(图标 + 文本)
    png = await adapter.screenshot()
    (row,) = await adapter.find_elements(wlan_row)
    left, top, right, bottom = row.bounds
    x0, y0 = max(0, left - 130), max(0, top - 15)
    x1, y1 = min(1080, right + 60), min(2400, bottom + 15)
    frame = decode_rgb(png, what="操作时截图")
    template_png = png_bytes(frame[y0:y1, x0:x1])

    store = TemplateStore(mobile_config.assets_dir)
    logical = "demo/wlan-target.png"
    existing = store.list_templates().get(logical)
    if existing:
        loaded = store.update(
            logical,
            template_png,
            existing["image_sha256"],
            source_width=1080,
            source_height=2400,
            orientation="portrait",
            app_package="com.android.settings",
        )
    else:
        loaded = store.create(
            logical,
            template_png,
            source_width=1080,
            source_height=2400,
            orientation="portrait",
            app_package="com.android.settings",
        )
    assert loaded.width == x1 - x0

    image_target = Target(Locator.IMAGE, "demo/wlan-target.png")

    # 图像查询:匹配数量与位置与裁剪区一致
    (handle,) = await adapter.find_elements(image_target)
    assert handle.bounds == (x0, y0, x1 - x0, y1 - y0)
    assert await adapter.is_visible(image_target) is True

    # 图像点击:发送到匹配中心
    await adapter.click(image_target)

    # 后置业务断言:进入 WLAN 子页
    advanced = Target(Locator.TEXT, "网络加速")
    assert await adapter.wait_for(advanced) is True
