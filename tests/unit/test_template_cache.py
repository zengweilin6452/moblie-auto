"""P2 模板解析缓存:命中免重复解析,清单版本切换即失效(plan image-v2 P2)。"""

import cv2
import numpy as np
from fakes import FakeBackend, make_adapter

from mobile_auto.assets import TemplateStore
from mobile_auto.config import MobileConfig
from mobile_auto.contracts import Locator, Target


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


def make_screen(seed: int = 1, w: int = 200, h: int = 150) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def make_patch(seed: int = 7, size: int = 20) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, size=(size, size, 3), dtype=np.uint8)


def patched_screen(patch: np.ndarray, at=(100, 50)) -> np.ndarray:
    screen = make_screen()
    px, py = at
    screen[py : py + patch.shape[0], px : px + patch.shape[1]] = patch
    return screen


async def test_cache_hit_skips_reparse_and_sees_version_switch(tmp_path, monkeypatch):
    """同版本重复匹配命中缓存(不再 load);清单切换后自动重解析新版本。"""
    patch = make_patch()
    screen = patched_screen(patch)
    config = MobileConfig(device_serial="SERIAL1", assets_dir=tmp_path / "assets")
    backend = FakeBackend(screen=(200, 150), screenshots=[png_bytes(screen)] * 6)
    adapter, _ = make_adapter(config=config, backend=backend)

    store = TemplateStore(config.assets_dir)
    store.create("t.png", png_bytes(patch), source_width=200, source_height=150)
    target = Target(Locator.IMAGE, "t.png")

    load_calls = []
    real_load = TemplateStore.load

    def counting_load(self, logical):
        load_calls.append(logical)
        return real_load(self, logical)

    monkeypatch.setattr(TemplateStore, "load", counting_load)

    assert await adapter.is_visible(target)  # 未命中:完整 load + 解析
    assert await adapter.is_visible(target)  # 命中:不再 load
    assert await adapter.is_visible(target)
    assert load_calls == ["t.png"]

    # 模板更新为新版本:清单 version_id 变化,缓存失效。update 内部也会
    # load 一次(更新后校验读),故共 3 次:首匹配 + update 内部 + 失效重载;
    # 新模板(seed 99)不在画面上,可见性变为 False 也证明匹配用的确是新版本
    other = make_patch(seed=99)
    store.update(
        "t.png", png_bytes(other), store.list_templates()["t.png"]["image_sha256"],
        source_width=200, source_height=150,
    )
    assert not await adapter.is_visible(target)
    assert load_calls == ["t.png"] * 3


async def test_cache_rejects_unregistered_template_each_time(tmp_path):
    """未登记模板每次都显式拒绝,缓存不吞掉 InvalidTemplate。"""
    config = MobileConfig(device_serial="SERIAL1", assets_dir=tmp_path / "assets")
    backend = FakeBackend(screen=(200, 150), screenshots=[png_bytes(make_screen())] * 2)
    adapter, _ = make_adapter(config=config, backend=backend)

    from mobile_auto import InvalidTemplate

    target = Target(Locator.IMAGE, "missing.png")
    for _ in range(2):
        try:
            await adapter.is_visible(target)
            raise AssertionError("应当显式拒绝")
        except InvalidTemplate:
            pass
