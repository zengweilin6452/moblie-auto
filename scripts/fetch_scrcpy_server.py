"""提取 scrcpy server 资产(工单 01;P2 上游迁移补 upstream 项)。

wetest-scrcpy==0.4.0 的 wheel 未打包 scrcpy-server(上游打包回归),
0.3.8 wheel 含协议匹配(1.2.4 + udt_socket_name)的 server。本脚本从
0.3.8 wheel 提取两个文件到运行时读取的 src/mobile_auto/stream/assets/,
幂等:已存在且摘要一致则跳过。

upstream 项(stream_backend="upstream" 用):上游官方 server v5.0.1
(Android 16 QPR2+/17 兼容修复,Apache-2.0),从 GitHub releases 下载;
GitHub 不可达时可手工放置后重跑校验。

用法:
  python scripts/fetch_scrcpy_server.py            # 经 pip 从 PyPI 下载
  python scripts/fetch_scrcpy_server.py --wheel 本地wheel路径
  python scripts/fetch_scrcpy_server.py --upstream # 补齐/校验上游 server
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# 登记来源与摘要(PROVENANCE 同步维护)
SOURCE = "wetest-scrcpy==0.3.8 wheel: wetest/scrcpy/resource/android/"
FILES = {
    "scrcpy-server": "6a805e76ea8683223b9c63a683350cd7ef7434a188423ab3dcc7eec2a134dcb2",
    "libjpeg-turbo.so": "b26ab7d352be585714c76c6c74a5a1e3098f9d32b8d81219b35cf4be6f8fe952",
}

UPSTREAM_FILE = "scrcpy-server-upstream.jar"
UPSTREAM_SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"
UPSTREAM_VERSION = "5.0.1"
UPSTREAM_URL = (
    "https://github.com/Genymobile/scrcpy/releases/download/"
    f"v{UPSTREAM_VERSION}/scrcpy-server-v{UPSTREAM_VERSION}"
)

ROOT = Path(__file__).resolve().parent.parent
# 运行时读取目录(stream/channel.py 的 _ASSETS_DIR);写别处修不了缺失资产
TARGET_DIR = ROOT / "src" / "mobile_auto" / "stream" / "assets"


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


def ensure_upstream() -> None:
    """上游 server:存在且摘要一致跳过;缺失则下载;不一致报错。"""
    target = TARGET_DIR / UPSTREAM_FILE
    if target.is_file():
        actual = sha256_of(target)
        if actual == UPSTREAM_SHA256:
            print(f"{UPSTREAM_FILE} 已存在且摘要一致,跳过。")
            return
        sys.exit(
            f"{UPSTREAM_FILE} 摘要不符:期望 {UPSTREAM_SHA256},实际 {actual};"
            "请删除后重新获取"
        )
    try:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=60) as response:
            data = response.read()
    except OSError as exc:
        sys.exit(
            f"下载上游 server 失败({exc});GitHub 不可达时可手工下载 "
            f"{UPSTREAM_URL} 放置为 {target} 后重跑本脚本校验"
        )
    if hashlib.sha256(data).hexdigest() != UPSTREAM_SHA256:
        sys.exit("下载的上游 server 摘要不符,已丢弃。")
    target.write_bytes(data)
    print(f"已下载 {UPSTREAM_FILE} v{UPSTREAM_VERSION}({len(data)} 字节)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=None, help="本地 wheel 路径(离线用)")
    parser.add_argument(
        "--upstream", action="store_true",
        help="只补齐/校验上游 server(stream_backend=upstream 用)",
    )
    args = parser.parse_args()

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    if args.upstream:
        ensure_upstream()
        return 0
    if all(
        (TARGET_DIR / name).is_file() and sha256_of(TARGET_DIR / name) == digest
        for name, digest in FILES.items()
    ):
        print("scrcpy 资产已存在且摘要一致,跳过。")
    else:
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
    ensure_upstream()  # 主路径顺带确保上游项齐备(幂等)
    print(f"完成,资产目录:{TARGET_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
