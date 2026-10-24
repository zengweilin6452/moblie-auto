"""模板资产库的外部行为:版本化、原子切换、摘要冲突与路径安全。"""

import json
import threading

import cv2
import numpy as np
import pytest

from mobile_auto import InvalidTemplate, StaleHandle
from mobile_auto.imaging.store import TemplateStore, validate_logical_path


def png_bytes(seed: int, size: int = 24) -> bytes:
    arr = np.random.default_rng(seed).integers(
        0, 255, size=(size, size, 3), dtype=np.uint8
    )
    ok, buf = cv2.imencode(".png", arr[:, :, ::-1])
    assert ok
    return buf.tobytes()


CAPTURE = dict(source_width=1080, source_height=2400, app_package="com.demo")


def make_store(tmp_path):
    return TemplateStore(tmp_path / "assets")


def test_create_registers_version_and_load_verifies(tmp_path):
    store = make_store(tmp_path)
    png = png_bytes(1)
    loaded = store.create("buttons/confirm.png", png, orientation="portrait", **CAPTURE)
    assert loaded.logical == "buttons/confirm.png"
    assert loaded.png == png
    assert loaded.meta["schema_version"] == 1
    assert loaded.meta["capture_time"]
    assert store.list_templates()["buttons/confirm.png"]["version_id"] == loaded.version_id


def test_create_rejects_duplicate_name(tmp_path):
    store = make_store(tmp_path)
    store.create("a.png", png_bytes(2), **CAPTURE)
    with pytest.raises(InvalidTemplate, match="同名模板已存在"):
        store.create("a.png", png_bytes(3), **CAPTURE)


def test_update_switches_version_and_keeps_old_files(tmp_path):
    store = make_store(tmp_path)
    old = store.create("a.png", png_bytes(2), **CAPTURE)
    old_dir = tmp_path / "assets" / "versions" / old.version_id
    new = store.update("a.png", png_bytes(3), old.sha256, **CAPTURE)
    assert new.version_id != old.version_id
    assert new.sha256 != old.sha256
    assert store.load("a.png").version_id == new.version_id
    assert (old_dir / "image.png").exists()  # 旧版本不被自动删除


def test_update_with_wrong_old_sha_conflicts_and_keeps_old(tmp_path):
    store = make_store(tmp_path)
    old = store.create("a.png", png_bytes(2), **CAPTURE)
    with pytest.raises(StaleHandle, match="摘要与期望不符"):
        store.update("a.png", png_bytes(3), "f" * 64, **CAPTURE)
    assert store.load("a.png").version_id == old.version_id  # 旧版本保持可见


def test_failed_update_leaves_no_mixed_state(tmp_path):
    """新版本写入失败(损坏 PNG)时,清单仍指旧版本,无'新图配旧摘要'。"""
    store = make_store(tmp_path)
    old = store.create("a.png", png_bytes(2), **CAPTURE)
    with pytest.raises(InvalidTemplate):
        store.update("a.png", b"corrupt", old.sha256, **CAPTURE)
    loaded = store.load("a.png")
    assert loaded.version_id == old.version_id
    assert loaded.sha256 == old.sha256
    versions = list((tmp_path / "assets" / "versions").iterdir())
    assert len(versions) == 1  # 校验在写入前发生


def test_load_detects_tampered_image(tmp_path):
    store = make_store(tmp_path)
    loaded = store.create("a.png", png_bytes(4), **CAPTURE)
    image_path = tmp_path / "assets" / "versions" / loaded.version_id / "image.png"
    image_path.write_bytes(png_bytes(5))  # 直接改动版本文件
    with pytest.raises(InvalidTemplate, match="摘要不符"):
        store.load("a.png")


def test_load_detects_tampered_meta_size(tmp_path):
    store = make_store(tmp_path)
    loaded = store.create("a.png", png_bytes(4), **CAPTURE)
    meta_path = tmp_path / "assets" / "versions" / loaded.version_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["width"] = 999
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(InvalidTemplate, match="尺寸与元数据不符"):
        store.load("a.png")


@pytest.mark.parametrize(
    ("logical",),
    [
        ("../escape.png",),
        ("a/../../escape.png",),
        ("/abs/escape.png",),
        ("C:/escape.png",),
        ("back\\slash.png",),
        ("no-ext",),
        ("",),
        ("./x.png",),
    ],
)
def test_path_traversal_and_absolute_rejected(tmp_path, logical):
    store = make_store(tmp_path)
    with pytest.raises(InvalidTemplate):
        store.create(logical, png_bytes(6), **CAPTURE)
    with pytest.raises(InvalidTemplate):
        store.load(logical)


def test_manifest_entry_tampering_rejected(tmp_path):
    store = make_store(tmp_path)
    store.create("a.png", png_bytes(7), **CAPTURE)
    manifest_path = tmp_path / "assets" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["templates"]["../evil.png"] = {
        "version_id": "0" * 32,
        "image_sha256": "0" * 64,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InvalidTemplate, match="非法段"):
        store.list_templates()


def test_symlink_escape_rejected(tmp_path):
    store = make_store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(png_bytes(8))
    link_dir = tmp_path / "assets" / "link"
    link_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")
    with pytest.raises(InvalidTemplate):
        store.load("link/secret.png")


def test_concurrent_updates_one_wins_one_conflicts(tmp_path):
    store = make_store(tmp_path)
    old = store.create("a.png", png_bytes(9), **CAPTURE)
    results = []
    barrier = threading.Barrier(2)

    def updater(seed):
        barrier.wait()
        try:
            new = store.update("a.png", png_bytes(seed), old.sha256, **CAPTURE)
            results.append(("ok", new.version_id))
        except StaleHandle:
            results.append(("conflict", None))

    threads = [threading.Thread(target=updater, args=(s,)) for s in (10, 11)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["conflict", "ok"]  # 资产锁内先到先得,后到摘要冲突
    versions = list((tmp_path / "assets" / "versions").iterdir())
    assert len(versions) == 2  # 旧版 + 恰一个新版


def test_validate_logical_path_normalizes():
    assert validate_logical_path("buttons/confirm.png") == "buttons/confirm.png"
    assert validate_logical_path("a.png") == "a.png"
