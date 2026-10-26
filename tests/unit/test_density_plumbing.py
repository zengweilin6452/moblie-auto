"""工单 02:密度来源、会话缓存与参数贯通(后端/会话/适配器/viewer)。

探针闸门未执行(无设备),本文件覆盖不依赖真机的部分:
`wm density` 解析、会话缓存与刷新调用点、模板 meta → 匹配参数、
viewer 查询复用 selectors.image_match_kwargs 以及快照密度落库。
"""

import cv2
import numpy as np
import pytest
from fakes import FakeBackend, make_adapter, make_session

from mobile_auto import Locator, MobileConfig, Target
from mobile_auto.core import selectors
from mobile_auto.core.backend import parse_wm_density
from mobile_auto.imaging import engine
from mobile_auto.imaging.store import TemplateStore
from mobile_auto.viewer.app import (
    _crop_and_register,
    _query_payload,
    capture_snapshot_sync,
)
from mobile_auto.viewer.store import SnapshotStore

W, H = 320, 400
PATCH_AT = (120, 100)
SIZE = 40
SRC_DENSITY = 440
CUR_DENSITY = 480


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


def make_patch(seed: int = 7) -> np.ndarray:
    gray = cv2.GaussianBlur(
        np.random.default_rng(seed).integers(70, 180, size=(SIZE, SIZE), dtype=np.uint8),
        (0, 0),
        1.5,
    )
    patch = np.stack([gray, gray, gray], axis=-1)
    patch[8:20, 8:20] = (40, 120, 220)
    patch[24:34, 24:34] = (220, 60, 60)
    return patch


def make_frame(seed: int = 1) -> np.ndarray:
    frame = np.random.default_rng(seed).integers(0, 255, size=(H, W, 3), dtype=np.uint8)
    x, y = PATCH_AT
    frame[y : y + SIZE, x : x + SIZE] = make_patch()
    return frame


def density_scaled(frame: np.ndarray, factor: float) -> np.ndarray:
    scaled = cv2.resize(frame, None, fx=factor, fy=factor, interpolation=cv2.INTER_LINEAR)
    sh, sw = scaled.shape[:2]
    y0, x0 = (sh - H) // 2, (sw - W) // 2
    return np.ascontiguousarray(scaled[y0 : y0 + H, x0 : x0 + W])


def cross_density_pngs():
    frame = make_frame()
    x, y = PATCH_AT
    template = np.ascontiguousarray(frame[y : y + SIZE, x : x + SIZE])
    return png_bytes(density_scaled(frame, CUR_DENSITY / SRC_DENSITY)), png_bytes(template)


# ── wm density 解析与后端实现 ───────────────────────────────


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Physical density: 440\nOverride density: 480\n", 480),
        ("Physical density: 440\n", 440),
        ("Physical density: 440\nOverride density: 0\n", 440),
        ("", None),
        ("garbage", None),
        ("Physical density: abc", None),
    ],
)
def test_parse_wm_density_prefers_override(output, expected):
    assert parse_wm_density(output) == expected


def test_fake_backend_density_default_and_override():
    assert FakeBackend().screen_density() == 420
    assert FakeBackend(density=480).screen_density() == 480


# ── 会话缓存与刷新 ─────────────────────────────────────────


def test_session_caches_density_and_refresh_rereads():
    session, backend = make_session(backend=FakeBackend(density=440))
    session.start()
    assert session.density(backend) == 440
    assert backend.density_calls == 1
    assert session.density(backend) == 440  # 会话内缓存,不重复读
    assert backend.density_calls == 1

    backend.density = 480
    assert session.density(backend) == 440  # 缓存未刷新
    assert session.refresh_density(backend) == 480  # 显式刷新(真实调用点见 viewer)
    assert session.density(backend) == 480
    assert backend.density_calls == 2


def test_session_density_failure_degrades_to_none():
    class BrokenDensity(FakeBackend):
        def screen_density(self):
            self.density_calls += 1
            raise RuntimeError("wm density 不可用")

    session, backend = make_session(backend=BrokenDensity())
    session.start()
    assert session.density(backend) is None  # 不报错,退化为旧行为
    assert session.density(backend) is None  # 失败不缓存,下次重试
    assert backend.density_calls == 2


# ── 适配器参数贯通 ─────────────────────────────────────────


def _density_env(tmp_path, meta_density):
    current_png, template_png = cross_density_pngs()
    assets = tmp_path / "assets"
    TemplateStore(assets).create(
        "demo/target.png",
        template_png,
        source_width=W,
        source_height=H,
        app_package="com.demo",
        density=meta_density,
    )
    backend = FakeBackend(screen=(W, H), screenshots=[current_png] * 3, density=CUR_DENSITY)
    config = MobileConfig(device_serial="SERIAL1", assets_dir=assets)
    return make_adapter(config=config, backend=backend)


async def test_adapter_uses_density_for_meta_with_density(tmp_path):
    adapter, backend = _density_env(tmp_path, SRC_DENSITY)
    handles = await adapter.find_elements(Target(Locator.IMAGE, "demo/target.png"))
    assert len(handles) == 1
    assert abs(handles[0].bounds[0] + SIZE // 2 - 138) <= 2  # 与引擎用例同一几何
    assert backend.density_calls == 1


async def test_adapter_old_template_without_density_skips_device_read(tmp_path):
    """旧 meta(无 density)不探测密度,行为与现在完全一致。"""
    adapter, backend = _density_env(tmp_path, None)
    assert await adapter.find_elements(Target(Locator.IMAGE, "demo/target.png")) == []
    assert backend.density_calls == 0


async def test_image_handle_revalidates_under_density_shift(tmp_path):
    """句柄重校验与查询用同一组密度:密度差下仍命中同一实例,不误报过期。"""
    adapter, backend = _density_env(tmp_path, SRC_DENSITY)
    (handle,) = await adapter.find_elements(Target(Locator.IMAGE, "demo/target.png"))
    await handle.click()
    assert backend.taps
    assert abs(backend.taps[0][0] - 138) <= 2 and abs(backend.taps[0][1] - 113) <= 2
    assert backend.density_calls == 1  # 会话缓存:查询与重校验共用


# ── viewer:查询参数复用、快照与模板登记 ────────────────────


def test_viewer_query_reuses_shared_image_match_kwargs(client, tmp_path, monkeypatch):
    """viewer 验证查询必须复用 selectors.image_match_kwargs(同一份真源)。"""
    _, session, backend = client
    backend.density = CUR_DENSITY
    store = TemplateStore(tmp_path / "assets")
    store.create(
        "demo/target.png",
        png_bytes(make_patch()),
        source_width=150,
        source_height=200,
        density=SRC_DENSITY,
    )

    recorded = {}
    original = selectors.image_match_kwargs

    def spy(meta, target, **kwargs):
        recorded.update(original(meta, target, **kwargs))
        return original(meta, target, **kwargs)

    seen = {}

    def fake_match(png, template_png, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(selectors, "image_match_kwargs", spy)
    monkeypatch.setattr(engine, "match_template", fake_match)

    _query_payload(
        backend,
        session,
        SnapshotStore(),
        Target(Locator.IMAGE, "demo/target.png"),
    )

    assert seen == recorded  # 传给引擎的就是共享函数产出的 kwargs
    assert seen["source_density"] == SRC_DENSITY
    assert seen["current_density"] == CUR_DENSITY


def test_capture_and_register_carry_snapshot_density(client):
    _, session, backend = client
    backend.density = CUR_DENSITY
    snapshot = capture_snapshot_sync(session, with_tree=False)
    assert snapshot.density == CUR_DENSITY

    loaded = _crop_and_register(
        session, snapshot, "demo/crop.png", [10, 20, 60, 40], {}, None
    )
    assert loaded.meta["density"] == CUR_DENSITY
    assert TemplateStore(session.config.assets_dir).load("demo/crop.png").meta[
        "density"
    ] == CUR_DENSITY
    session.close()
