"""IMAGE 流程示例:模板已存在时点击视觉目标(模板制作见查看器网页)。

运行:
    python examples/image_flow.py --template demo/target.png [--config ...]

模板为 assets_dir 内的相对路径(与查看器"复制 IMAGE 定位"一致);
阈值/搜索区域可选。
"""

import argparse
import asyncio

from mobile_auto import DeviceSession, Locator, MobileAdapter, Target, load_config


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="TOML 配置文件路径")
    parser.add_argument("--template", required=True, help="模板相对路径,如 demo/target.png")
    parser.add_argument("--confidence", type=float, default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    session = DeviceSession(config)
    session.start()
    try:
        adapter = MobileAdapter(session)
        await adapter.goto("")
        target = Target(Locator.IMAGE, args.template, confidence=args.confidence)
        assert await adapter.wait_for(target), "图像目标未出现"
        await adapter.click(target)
        png = await adapter.screenshot()
        assert png.startswith(b"\x89PNG"), "截图应为 PNG"
        print(f"IMAGE 流程示例通过:点击 {args.template}")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
