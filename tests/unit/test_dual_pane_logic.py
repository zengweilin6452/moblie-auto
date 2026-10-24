"""双栏联动前端纯函数的 node 单测包装(工单 05);无 node 时跳过。"""

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_JS = Path(__file__).parent / "dual_pane_logic.test.js"


def test_dual_pane_logic_node_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不可用,前端纯函数单测未执行")
    result = subprocess.run(
        [node, str(_TEST_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"node 单测失败:\n{result.stdout}\n{result.stderr}"
    assert "tests passed" in result.stdout
