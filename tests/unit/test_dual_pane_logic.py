"""前端纯函数的 node 单测包装;无 node 时跳过。"""

import shutil
import subprocess
from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).parent
_NODE_SUITES = ("dual_pane_logic.test.js", "snapshot_freeze.test.js")


def _run_node_suite(name: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不可用,前端纯函数单测未执行")
    result = subprocess.run(
        [node, str(_UNIT_DIR / name)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"node 单测失败:\n{result.stdout}\n{result.stderr}"
    assert "tests passed" in result.stdout


@pytest.mark.parametrize("suite", _NODE_SUITES)
def test_frontend_node_suite(suite):
    _run_node_suite(suite)
