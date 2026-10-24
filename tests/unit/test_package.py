"""包级外部行为:公开导出可导入,且导入本身无设备副作用。"""

import subprocess
import sys

import mobile_auto


def test_public_exports_importable():
    for name in mobile_auto.__all__:
        assert getattr(mobile_auto, name) is not None


def test_import_has_no_device_side_effects():
    """导入 mobile_auto 不得连带导入设备栈(uiautomator2/adbutils/CLI)。"""
    code = (
        "import sys, mobile_auto; "
        "banned = [m for m in sys.modules "
        "if m == 'mobile_auto.cli' or m.split('.')[0] in {'uiautomator2', 'adbutils'}]; "
        "sys.exit(f'banned: {banned}' if banned else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
