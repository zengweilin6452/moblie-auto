"""提取 scrcpy server 资产(工单 01)。

wetest-scrcpy==0.4.0 的 wheel 未打包 scrcpy-server(上游打包回归),
0.3.8 wheel 含协议匹配(1.2.4 + udt_socket_name)的 server。本脚本从
0.3.8 wheel 提取两个文件到 src/mobile_auto/scrcpy_assets/,幂等:
已存在且摘要一致则跳过。

用法:
  python scripts/fetch_scrcpy_server.py            # 经 pip 从 PyPI 下载
  python scripts/fetch_scrcpy_server.py --wheel 本地wheel路径
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# 登记来源与摘要(PROVENANCE 同步维护)
SOURCE = "wetest-scrcpy==0.3.8 wheel: wetest/scrcpy/resource/android/"
FILES = {
    "scrcpy-server": "6a805e76ea8683223b9c63a683350cd7ef7434a188423ab3dcc7eec2a134dcb2",
    "libjpeg-turbo.so": "b26ab7d352be585714c76c6c74a5a1e3098f9d32b8d81219b35cf4be6f8fe952",
}

ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = ROOT / "src" / "mobile_auto" / "scrcpy_assets"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_wheel(dest: Path) -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "download", "wetest-scrcpy==0.3.8",
         "--no-deps", "-d", str(dest)],
        capture_output=True, text=True,
    )
    wheels = list(dest.glob("*.whl"))
    if result.returncode != 0 or not wheels:
        sys.exit(f"pip download 失败:{result.stderr.strip()[:500]}")
    return wheels[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=None, help="本地 wheel 路径(离线用)")
    args = parser.parse_args()

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    if all(
        (TARGET_DIR / name).is_file() and sha256_of(TARGET_DIR / name) == digest
        for name, digest in FILES.items()
    ):
        print("scrcpy 资产已存在且摘要一致,跳过。")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        wheel = args.wheel if args.wheel else download_wheel(tmp_dir)
        with zipfile.ZipFile(wheel) as bundle:
            members = {
                name.split("/")[-1]: name
                for name in bundle.namelist()
                if name.startswith("wetest/scrcpy/resource/android/")
                and not name.endswith("/")
            }
            for logical, digest in FILES.items():
                if logical not in members:
                    sys.exit(f"wheel 中缺少 {logical}(内含:{sorted(members)})")
                out = TARGET_DIR / logical
                bundle.extract(members[logical], tmp_dir)
                extracted = tmp_dir / members[logical]
                actual = sha256_of(extracted)
                if actual != digest:
                    sys.exit(f"{logical} 摘要不符:期望 {digest},实际 {actual}")
                out.write_bytes(extracted.read_bytes())
                print(f"已提取 {logical}({out.stat().st_size} 字节)")
    print(f"完成,资产目录:{TARGET_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
