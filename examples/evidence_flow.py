"""失败证据钩子示例(P1 工单 06):pytest --mobile-evidence 运行。

运行:
    pytest examples/evidence_flow.py --mobile-evidence -q

mobile_session fixture 演示内网接入形态:session 级设备会话 +
function 级适配器(资源所有权约定见 docs/integration.md)。第二个
用例故意失败,演示钩子把失败瞬间的设备画面(scrcpy 视频帧优先)
与 UI 树附加到报告 section 与产物目录。
"""

import pytest

from mobile_auto import DeviceSession, load_config


@pytest.fixture(scope="session")
def mobile_session(request):
    config = load_config(getattr(request.config.option, "mobile_config", None))
    session = DeviceSession(config)
    session.start()
    yield session
    session.close()


@pytest.fixture
def adapter(mobile_session):
    from mobile_auto import MobileAdapter

    return MobileAdapter(mobile_session)


def test_passing_flow(adapter):
    """通过的用例:不产生任何证据。"""
    assert adapter.platform == "android"


def test_failing_flow_collects_evidence(adapter):
    """故意失败:--mobile-evidence 开启时自动附加失败现场。"""
    text = adapter_platform_note(adapter)
    assert text == "这个文本不存在", f"得到 {text!r},预期失败并采集证据"


def adapter_platform_note(adapter) -> str:
    return adapter.platform
