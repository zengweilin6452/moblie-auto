"""真机失败用例产出证据(工单 05)。

第一个用例故意失败(strict xfail):失败后在会话关闭前收集证据,
登记运行清单;第二个用例读取清单断言证据存在。
"""

import pytest

from mobile_auto import Locator, Target
from mobile_auto.evidence.collector import read_manifest


@pytest.mark.device
@pytest.mark.xfail(reason="故意失败以验证失败证据产出(工单 05)", strict=True)
async def test_intentional_failure_records_evidence(adapter, evidence):
    await adapter.goto("")
    assert await adapter.wait_for(Target(Locator.TEXT, "WLAN")) is True
    png = await adapter.screenshot()
    evidence.record_frame(png, "op-frame")
    raise AssertionError("故意失败:验证失败证据被收集")


@pytest.mark.device
def test_evidence_manifest_has_entry_from_intentional_failure(mobile_config):
    entries = read_manifest(mobile_config.artifacts_dir)
    assert entries, "运行清单为空"
    entry = entries[-1]
    assert entry["failure_type"] == "Failure"  # pytest 报告文本作为失败来源
    assert entry["device_serial"] == mobile_config.device_serial
    assert "operation-op-frame.png" in entry["artifacts"]
    assert "failure-frame.png" in entry["artifacts"]
