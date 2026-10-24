"""失败证据钩子真机 e2e(工单 06):--mobile-evidence 附加视频帧证据。

子进程运行一个故意失败的用例(置于 tests/device 下以继承 conftest);
它先经驱动截图(auto 策略启动 scrcpy 通道),失败后证据应取到
视频帧(frame_source=scrcpy-video)。
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.device

_CHILD = '''
import asyncio

import pytest


def test_failing_with_stream_history(adapter):
    # 先走一次驱动截图:触发 scrcpy 通道启动(auto 策略)
    png = asyncio.run(adapter.screenshot())
    assert png[:4] == bytes([0x89]) + b"PNG"
    assert False, "故意失败:验证钩子附加视频帧证据"
'''


def test_plugin_attaches_video_frame_evidence(mobile_config):
    mobile_config  # noqa: B018  # 仅用于门控(无设备时跳过)
    child = Path(__file__).parent / "_child_evidence_test.py"
    child.write_text(textwrap.dedent(_CHILD), encoding="utf-8")
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", str(child),
                # -p 显式加载:不依赖 editable 安装元数据中 pytest11 入口点的鲜度
                "-p", "mobile_auto.evidence.pytest_plugin",
                "--mobile-evidence", "-q",
                "--mobile-config", "tests/device/local.toml",
            ],
            capture_output=True, text=True, timeout=180,
            cwd=Path(__file__).parents[2],
        )
    finally:
        child.unlink(missing_ok=True)
    out = result.stdout + result.stderr
    child_failed = "FAILED" in out and "_child_evidence_test" in out
    assert child_failed, f"子用例应失败:\n{out[-1500:]}"
    assert "mobile-auto-evidence" in out
    assert "frame_source: scrcpy-video" in out, f"应取到视频帧:\n{out[-1500:]}"
