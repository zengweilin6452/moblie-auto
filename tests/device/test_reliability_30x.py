"""真机 30 次重复验收(工单 13;plan 12.1 可靠性口径)。

原生流程与 IMAGE 流程各重复 30 次,逐次归因;零未解决工具原因失败才通过。
运行:
    python -m pytest tests/device/test_reliability_30x.py -m device \
        --mobile-config tests/device/local.toml -q --no-header -rA
逐次结果以固定格式输出"NATIVE n: PASS/FAIL <原因>"供验证记录引用。
"""

import asyncio
import time

import pytest

from mobile_auto import Locator, Target
from mobile_auto.assets import TemplateStore


async def _native_once(adapter) -> None:
    await adapter.goto("")
    wlan = Target(Locator.TEXT, "WLAN")
    assert await adapter.wait_for(wlan), "主页 WLAN 未出现"
    await adapter.click(wlan)
    advanced = Target(Locator.TEXT, "高级设置")
    assert await adapter.wait_for(advanced), "WLAN 子页未打开"
    assert await adapter.get_text(advanced) == "高级设置"


async def _image_once(adapter, mobile_config, logical: str) -> None:
    await adapter.goto("")
    target = Target(Locator.IMAGE, logical)
    assert await adapter.wait_for(target), "图像目标未出现"
    await adapter.click(target)
    assert await adapter.wait_for(Target(Locator.TEXT, "高级设置")), "子页未打开"


@pytest.mark.device
def test_native_30x(adapter, mobile_config, request):
    results = []
    for i in range(1, 31):
        start = time.monotonic()
        try:
            asyncio.run(_native_once(adapter))
            results.append(f"NATIVE {i}: PASS ({time.monotonic() - start:.1f}s)")
        except Exception as exc:  # noqa: BLE001 - 逐次归因记录
            results.append(f"NATIVE {i}: FAIL {type(exc).__name__}: {exc}")
    print("\n" + "\n".join(results))
    failures = [r for r in results if "FAIL" in r]
    if failures:
        pytest.fail("原生流程 30 次存在失败(逐次归因见输出):\n" + "\n".join(failures))


@pytest.mark.device
def test_image_30x(adapter, mobile_config):
    # 模板:真机实时制作一次(整行 WLAN,与工单 11 同源)
    import asyncio

    asyncio.run(adapter.goto(""))
    assert asyncio.run(adapter.wait_for(Target(Locator.TEXT, "WLAN"))), "主页未就绪"
    png = asyncio.run(adapter.screenshot())
    rows = asyncio.run(adapter.find_elements(Target(Locator.TEXT, "WLAN", index=0)))
    assert rows, "index=0 应返回 WLAN 句柄"
    row = rows[0]
    import cv2
    import numpy as np

    from mobile_auto.image import decode_rgb

    frame = decode_rgb(png, what="操作时截图")
    left, top, right, bottom = row.bounds
    region = [0, max(0, top - 10), right + 60, (bottom - top) + 20]
    crop = np.ascontiguousarray(
        frame[region[1] : region[1] + region[3], region[0] : region[0] + region[2]]
    )
    ok, buf = cv2.imencode(".png", crop[:, :, ::-1])
    assert ok
    import time as _time

    logical = f"reliability/native-{_time.time_ns() % 10_000_000}.png"
    TemplateStore(mobile_config.assets_dir).create(
        logical,
        buf.tobytes(),
        source_width=1080,
        source_height=2400,
        orientation="portrait",
        app_package="com.android.settings",
    )

    results = []
    for i in range(1, 31):
        start = time.monotonic()
        try:
            asyncio.run(_image_once(adapter, mobile_config, logical))
            results.append(f"IMAGE {i}: PASS ({time.monotonic() - start:.1f}s)")
        except Exception as exc:  # noqa: BLE001
            results.append(f"IMAGE {i}: FAIL {type(exc).__name__}: {exc}")
    print("\n" + "\n".join(results))
    failures = [r for r in results if "FAIL" in r]
    if failures:
        pytest.fail("IMAGE 流程 30 次存在失败(逐次归因见输出):\n" + "\n".join(failures))
