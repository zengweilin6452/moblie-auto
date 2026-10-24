"""模板资产库:逻辑名引用不可变模板版本,清单原子切换(plan 6.3)。

布局(资产根目录 assets_dir 下):
  manifest.json                     清单:逻辑名 → 当前版本(原子替换)
  versions/<version_id>/image.png   版本图片(不可变)
  versions/<version_id>/meta.json   版本元数据(schema_version=1)

不变量:图片与元数据成对完整写入并校验通过后,清单才经 os.replace
原子切换;任一步失败旧版本保持可见,不存在"新图片配旧摘要"的中间状态。
更新在资产级文件锁内先校验旧摘要(乐观锁)。旧版本与证据永不自动删除。
运行时不编造采集环境;无元数据的旧模板经 create 显式登记后使用。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .errors import InvalidTemplate, StaleHandle
from .image import decode_rgb

MANIFEST_NAME = "manifest.json"
_SCHEMA_VERSION = 1
_ID_RE = re.compile(r"[0-9a-f]{32}")
_ORIENTATIONS = ("portrait", "landscape")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class LoadedTemplate:
    """加载通过的模板版本:内容摘要、尺寸与采集环境。"""

    logical: str
    version_id: str
    png: bytes
    sha256: str
    width: int
    height: int
    meta: dict


def validate_logical_path(logical: str) -> str:
    """校验模板逻辑名:资产根内相对 POSIX 路径,拒绝穿越与逃逸。"""
    if not isinstance(logical, str) or not logical.strip():
        raise InvalidTemplate("模板逻辑名必须是非空相对路径")
    if "\\" in logical or logical.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", logical):
        raise InvalidTemplate(f"模板逻辑名必须是 POSIX 相对路径:{logical!r}")
    if logical.startswith("./") or "/./" in logical or logical.endswith("/."):
        raise InvalidTemplate(f"模板逻辑名包含非法段:{logical!r}")
    pure = PurePosixPath(logical)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise InvalidTemplate(f"模板逻辑名包含非法段:{logical!r}")
    if not pure.name.endswith(".png") or pure.name == ".png":
        raise InvalidTemplate("模板逻辑名必须以 .png 结尾")
    return str(pure)


class TemplateStore:
    """资产根目录内的模板版本库;线程与进程安全(资产级锁 + 原子清单)。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._manifest_path = self._root / MANIFEST_NAME
        self._write_lock = threading.Lock()
        self._process_lock_path = self._root / "locks" / "assets.lock"

    @property
    def root(self) -> Path:
        """资产根目录(已解析绝对路径);解析缓存的键成分。"""
        return self._root

    # ── 查询 ─────────────────────────────────────────────────

    def list_templates(self) -> dict[str, dict]:
        """清单内容:{逻辑名: {version_id, image_sha256}};无清单为空。"""
        return dict(self._read_manifest()["templates"])

    def load(self, logical: str) -> LoadedTemplate:
        """加载当前版本;摘要或尺寸与元数据不符即拒绝(不静默容错)。"""
        logical = validate_logical_path(logical)
        entry = self._read_manifest()["templates"].get(logical)
        if entry is None:
            raise InvalidTemplate(f"模板未登记:{logical}")
        version_id = entry["version_id"]
        version_dir = self._version_dir(version_id)
        png = (version_dir / "image.png").read_bytes()
        meta = json.loads((version_dir / "meta.json").read_text(encoding="utf-8"))
        if meta.get("image_sha256") != hashlib.sha256(png).hexdigest():
            raise InvalidTemplate(
                f"模板 {logical} 内容与摘要不符;资产可能被改动,请重新登记"
            )
        expected_size = (meta.get("width"), meta.get("height"))
        actual_png = decode_rgb(png, what=f"模板 {logical}")
        if (actual_png.shape[1], actual_png.shape[0]) != expected_size:
            raise InvalidTemplate(
                f"模板 {logical} 尺寸与元数据不符:{actual_png.shape[1]}x{actual_png.shape[0]}"
                f" != {expected_size[0]}x{expected_size[1]}"
            )
        if meta.get("image_sha256") != entry.get("image_sha256"):
            raise InvalidTemplate(f"模板 {logical} 清单摘要与版本元数据不一致")
        return LoadedTemplate(
            logical=logical,
            version_id=version_id,
            png=png,
            sha256=meta["image_sha256"],
            width=expected_size[0],
            height=expected_size[1],
            meta=meta,
        )

    # ── 写入 ─────────────────────────────────────────────────

    def create(
        self,
        logical: str,
        png: bytes,
        *,
        source_width: int,
        source_height: int,
        orientation: str = "portrait",
        app_package: str = "",
        note: str = "",
    ) -> LoadedTemplate:
        """登记新模板(也是旧模板的专门导入步骤);同名默认拒绝。"""
        logical = validate_logical_path(logical)
        with self._asset_lock():
            if logical in self._read_manifest()["templates"]:
                raise InvalidTemplate(
                    f"同名模板已存在:{logical};更新须显式提供旧版本摘要"
                )
            version = self._build_version(logical, png, source_width, source_height,
                                          orientation, app_package, note)
            self._write_version_files(version)
            self._switch_manifest(logical, version, previous=None)
        return self.load(logical)

    def update(
        self,
        logical: str,
        png: bytes,
        expected_old_sha256: str,
        *,
        source_width: int,
        source_height: int,
        orientation: str = "portrait",
        app_package: str = "",
        note: str = "",
    ) -> LoadedTemplate:
        """更新为新版本:锁内校验旧摘要,成对写入校验后清单才原子切换。

        摘要与当前版本不符报 StaleHandle(冲突);任一步失败旧版本保持可见。
        """
        logical = validate_logical_path(logical)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_old_sha256 or ""):
            raise InvalidTemplate("expected_old_sha256 必须是 64 位十六进制摘要")
        with self._asset_lock():
            entry = self._read_manifest()["templates"].get(logical)
            if entry is None:
                raise InvalidTemplate(f"模板未登记:{logical}")
            if entry["image_sha256"] != expected_old_sha256:
                raise StaleHandle(
                    f"模板 {logical} 当前摘要与期望不符,请基于最新版本更新"
                    f"(当前 {entry['image_sha256'][:12]}…)"
                )
            version = self._build_version(logical, png, source_width, source_height,
                                          orientation, app_package, note)
            self._write_version_files(version)
            self._switch_manifest(logical, version, previous=entry)
        return self.load(logical)

    # ── 内部 ─────────────────────────────────────────────────

    def _build_version(self, logical, png, source_width, source_height,
                       orientation, app_package, note) -> dict:
        decoded = decode_rgb(png, what=f"模板 {logical}")
        width, height = int(decoded.shape[1]), int(decoded.shape[0])
        if not isinstance(source_width, int) or not isinstance(source_height, int):
            raise InvalidTemplate("采集分辨率必须是整数")
        if orientation not in _ORIENTATIONS:
            raise InvalidTemplate(f"orientation 必须是 {_ORIENTATIONS} 之一")
        return {
            "version_id": uuid.uuid4().hex,
            "logical": logical,
            "png": png,
            "meta": {
                "schema_version": _SCHEMA_VERSION,
                "logical": logical,
                "image_sha256": hashlib.sha256(png).hexdigest(),
                "width": width,
                "height": height,
                "source_width": source_width,
                "source_height": source_height,
                "orientation": orientation,
                "app_package": app_package,
                "capture_time": _utc_now(),
                "note": note,
            },
        }

    def _write_version_files(self, version: dict) -> None:
        """版本文件完整落盘并回读校验;失败抛错,清单尚未指向该版本。"""
        version_dir = self._version_dir(version["version_id"])
        version_dir.mkdir(parents=True, exist_ok=True)
        image_path = version_dir / "image.png"
        meta_path = version_dir / "meta.json"
        image_path.write_bytes(version["png"])
        meta_path.write_text(
            json.dumps(version["meta"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        reread = image_path.read_bytes()
        if hashlib.sha256(reread).hexdigest() != version["meta"]["image_sha256"]:
            raise InvalidTemplate("版本图片落盘校验失败;旧版本保持可见")

    def _switch_manifest(self, logical: str, version: dict, previous: dict | None) -> None:
        """清单原子切换(os.replace);仅在新版本完整落盘并校验后调用。"""
        manifest = self._read_manifest()
        manifest["templates"][logical] = {
            "version_id": version["version_id"],
            "image_sha256": version["meta"]["image_sha256"],
        }
        tmp = self._manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self._manifest_path)

    def _version_dir(self, version_id: str) -> Path:
        if not _ID_RE.fullmatch(version_id):
            raise InvalidTemplate(f"非法版本标识:{version_id!r}")
        path = self._root / "versions" / version_id
        resolved = path.resolve()
        if not str(resolved).startswith(str(self._root)):
            raise InvalidTemplate("版本目录逃逸出资产根")
        return path

    def _read_manifest(self) -> dict:
        path = self._root / MANIFEST_NAME
        if not path.is_file():
            return {"schema_version": _SCHEMA_VERSION, "templates": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        templates = data.get("templates", {})
        for logical, entry in templates.items():
            validate_logical_path(logical)
            if not _ID_RE.fullmatch(str(entry.get("version_id", ""))):
                raise InvalidTemplate(f"清单中存在非法版本标识:{logical}")
        return data

    def _asset_lock(self):
        """资产级锁:进程内互斥 + 跨进程文件区域锁(plan 6.3)。"""
        return _AssetWriteLock(self._write_lock, self._process_lock_path)


class _AssetWriteLock:
    """with 语义的组合锁;文件锁随句柄释放,不删除锁文件。"""

    def __init__(self, thread_lock: threading.Lock, process_lock_path: Path) -> None:
        self._thread_lock = thread_lock
        self._process_lock_path = process_lock_path
        self._handle = None

    def __enter__(self) -> _AssetWriteLock:
        self._thread_lock.acquire()
        try:
            import msvcrt

            self._process_lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self._process_lock_path, "a+b")
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                handle.close()
                raise InvalidTemplate(f"资产库正被其他进程写入:{exc}") from exc
            self._handle = handle
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            if self._handle is not None:
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                self._handle.close()
                self._handle = None
        finally:
            self._thread_lock.release()
