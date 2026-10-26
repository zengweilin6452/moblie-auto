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
    assert loaded.meta["schema_version"] == 3
    assert loaded.meta["capture_time"]
    assert "capture_center" not in loaded.meta  # 未提供时不含该字段(v1 兼容)
    assert "density" not in loaded.meta  # 未提供时不含该字段(旧模板兼容)
    assert store.list_templates()["buttons/confirm.png"]["version_id"] == loaded.version_id


def test_create_with_capture_center_roundtrip(tmp_path):
    """P4b:capture_center 写入 meta 并随 load 返回;JSON 落盘为列表。"""
    store = make_store(tmp_path)
    loaded = store.create(
        "a.png", png_bytes(1), capture_center=(0.25, -0.5), **CAPTURE
    )
    assert loaded.meta["capture_center"] == [0.25, -0.5]
    on_disk = json.loads(
        (tmp_path / "assets" / "versions" / loaded.version_id / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["capture_center"] == [0.25, -0.5]
    assert store.load("a.png").meta["capture_center"] == [0.25, -0.5]


@pytest.mark.parametrize("bad", ["x", 5, [1, 2, 3], [1], (0.1,), [float("nan"), 0]])
def test_create_rejects_bad_capture_center(tmp_path, bad):
    store = make_store(tmp_path)
    with pytest.raises(InvalidTemplate, match="capture_center"):
        store.create("a.png", png_bytes(2), capture_center=bad, **CAPTURE)
    assert store.list_templates() == {}  # 失败登记不留半成品


def test_create_and_update_density_roundtrip(tmp_path):
    """工单 02:密度写入 meta 并随 load 返回;update 同样携带(不静默沿用)。"""
    store = make_store(tmp_path)
    created = store.create("a.png", png_bytes(1), density=440, **CAPTURE)
    assert created.meta["density"] == 440
    on_disk = json.loads(
        (tmp_path / "assets" / "versions" / created.version_id / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["density"] == 440
    assert store.load("a.png").meta["density"] == 440

    updated = store.update("a.png", png_bytes(2), created.sha256, density=480, **CAPTURE)
    assert updated.meta["density"] == 480
    assert store.load("a.png").meta["density"] == 480


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "480"])
def test_create_rejects_bad_density(tmp_path, bad):
    store = make_store(tmp_path)
    with pytest.raises(InvalidTemplate, match="density"):
        store.create("a.png", png_bytes(2), density=bad, **CAPTURE)
    assert store.list_templates() == {}  # 失败登记不留半成品


def test_legacy_v2_meta_without_density_loads(tmp_path):
    """v2 旧 meta(无 density)加载不受影响,按 None 处理;手改非法值拒绝。"""
    store = make_store(tmp_path)
    loaded = store.create("a.png", png_bytes(2), density=440, **CAPTURE)
    meta_path = tmp_path / "assets" / "versions" / loaded.version_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["density"]
    meta["schema_version"] = 2
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert "density" not in store.load("a.png").meta

    meta["density"] = 0
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InvalidTemplate, match="density"):
        store.load("a.png")


def test_legacy_meta_without_capture_center_loads(tmp_path):
    """schema v1 旧模板(无 capture_center)加载不受影响;手改非法值拒绝。"""
    store = make_store(tmp_path)
    loaded = store.create("a.png", png_bytes(2), capture_center=(0.1, 0.1), **CAPTURE)
    meta_path = tmp_path / "assets" / "versions" / loaded.version_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["capture_center"]
    meta["schema_version"] = 1
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert "capture_center" not in store.load("a.png").meta

    meta["capture_center"] = "garbage"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InvalidTemplate, match="capture_center"):
        store.load("a.png")


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
