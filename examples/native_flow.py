"""原生流程示例:启动 → 定位 → 点击 → 断言 → 截图。

运行(项目根或安装了 mobile-auto 的环境):
    python examples/native_flow.py [--config path/to/mobile-auto.toml]

配置来源:--config 指定的 TOML,或环境变量 MOBILE_DEVICE_SERIAL /
MOBILE_APP_PACKAGE / MOBILE_APP_ACTIVITY,或默认值。
"""

import argparse
import asyncio

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="TOML 配置文件路径")
    args = parser.parse_args()
    config = load_config(args.config)
    session = DeviceSession(config)
    session.start()
    try:
        adapter = MobileAdapter(session)
        await adapter.goto("")
        wlan = Target(Locator.TEXT, "WLAN")
        assert await adapter.wait_for(wlan), "主页 WLAN 未出现"
        await adapter.click(wlan)
        advanced = Target(Locator.TEXT, "高级设置")
        assert await adapter.wait_for(advanced), "WLAN 子页未打开"
        text = await adapter.get_text(advanced)
        assert text == "高级设置", f"文本断言失败:{text!r}"
        png = await adapter.screenshot()
        assert png.startswith(b"\x89PNG"), "截图应为 PNG"
        print("原生流程示例通过:启动 → 定位 → 点击 → 断言 → 截图")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
