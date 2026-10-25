"""适配器层真机验证:P2-8 多元素句柄各自可用、P2-9 序号越界不泄漏 IndexError。

前置:真机 749622c9 在线、设置页(测试配置同 tests/device/local.toml)。
只读操作:find_elements/get_text/轮询,不点击、不改设备状态。
用法:python docs/snapshots/verify_adapter_device.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mobile_auto import DeviceSession, ElementNotFound, Locator, MobileAdapter, Target, load_config


async def main() -> int:
    config = dataclasses.replace(
        load_config("tests/device/local.toml"), wait_timeout=2, poll_interval=0.2
    )
    session = DeviceSession(config)
    session.start()
    try:
        import uiautomator2 as u2

        device = u2.connect(config.device_serial)
        if not device.info.get("screenOn"):
            device.screen_on()
        adapter = MobileAdapter(session)

        # ── P2-9:序号越界报 ElementNotFound,不泄漏 IndexError ──
        try:
            await adapter.click(Target(Locator.TEXT, "__绝不存在的文本__", index=5))
            print("FAIL  P2-9 越界未报错(不应发生)")
            return 1
        except ElementNotFound:
            print("PASS  P2-9 序号越界报 ElementNotFound")
        except IndexError as exc:
            print(f"FAIL  P2-9 泄漏 IndexError:{exc!r}")
            return 1

        # ── P2-8:多元素查询的句柄按各自序号重校验,互不串位 ──
        target = Target(Locator.U2_SELECTOR, '{"clickable": true}')
        handles = await adapter.find_elements(target)
        if len(handles) < 2:
            print(f"FAIL  P2-8 可点击元素不足 2 个(得到 {len(handles)}),无法验证")
            return 1
        indexes = [h.target.index for h in handles]
        print(f"INFO  可点击元素 {len(handles)} 个,句柄序号 {indexes[:6]}…")
        for i, handle in enumerate(handles[:3]):
            text = await handle.get_text()  # 旧实现第二只起立即 StaleHandle
            print(f"PASS  P2-8 句柄[{i}] 重校验+取文本成功 text={text!r}")
        return 0
    finally:
        await asyncio.to_thread(session.close)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
