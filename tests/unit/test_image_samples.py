"""真实正负样本回归(≥10 组,来源与登记见 tests/fixtures/images/README.md)。"""

import json
import pathlib
from typing import Any

import pytest

from mobile_auto.image import match_template

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "images"


def load_samples() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "samples.json").read_text(encoding="utf-8"))


SAMPLES = load_samples()


def test_sample_inventory_is_registered():
    assert len(SAMPLES) >= 10, "真实正负样本至少 10 组(plan 12.1)"
    assert any(s["expect"] == "positive" for s in SAMPLES)
    assert any(s["expect"] == "empty" for s in SAMPLES)


SAMPLE_IDS = [f"{s['template']}@{s['screenshot']}" for s in SAMPLES]


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_real_sample_matches_expected(sample):
    screenshot = (FIXTURES / sample["screenshot"]).read_bytes()
    template = (FIXTURES / sample["template"]).read_bytes()
    results = match_template(screenshot, template)
    if sample["expect"] == "positive":
        assert results, f"应匹配到目标:{sample}"
        box = sample["box"]
        cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
        top = results[0]
        assert abs(top.center[0] - cx) <= 2 and abs(top.center[1] - cy) <= 2, (
            f"中心偏差超过校准容差 2 像素:{top.center} vs {(cx, cy)}"
        )
    else:
        assert results == [], f"应无匹配:{sample.get('note', sample)}"


# P4a 跨分辨率验收:采集分辨率 1080x2400 的模板在缩放后的同源截图上匹配。
# 单尺度(不提供 source_resolution)必须失败,提供后必须命中且中心按比例对齐。
_CROSS_RES_CASES = [
    ("t-wlan-row.png", 1.25, 1),
    ("t-wlan-row.png", 1.15, 1),
    ("t-wlan-row.png", 0.85, 1),
    ("t-search-bar.png", 1.25, 1),
    ("t-icon-wlan.png", 1.25, 1),
]
_SOURCE_RESOLUTION = (1080, 2400)


@pytest.mark.parametrize(
    ("template_name", "factor", "expected_count"), _CROSS_RES_CASES,
    ids=[f"{t}@{f}x" for t, f, _ in _CROSS_RES_CASES],
)
def test_cross_resolution_real_sample(template_name, factor, expected_count):
    import cv2

    from mobile_auto.image import decode_rgb

    shot = decode_rgb((FIXTURES / "page-main.png").read_bytes(), what="截图")
    scaled = cv2.resize(shot, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    screenshot = cv2.imencode(".png", cv2.cvtColor(scaled, cv2.COLOR_RGB2BGR))[1].tobytes()
    template = (FIXTURES / template_name).read_bytes()

    # 单尺度路径:跨分辨率必然失败(P4a 的存在依据)
    assert match_template(screenshot, template) == []

    results = match_template(
        screenshot, template, source_resolution=_SOURCE_RESOLUTION
    )
    assert len(results) == expected_count
    box = next(s["box"] for s in SAMPLES if s["template"] == template_name
               and s["screenshot"] == "page-main.png" and s["expect"] == "positive")
    cx, cy = (box[0] + box[2] // 2) * factor, (box[1] + box[3] // 2) * factor
    assert abs(results[0].center[0] - cx) <= 2, f"中心偏差:{results[0].center} vs {(cx, cy)}"
    assert abs(results[0].center[1] - cy) <= 2, f"中心偏差:{results[0].center} vs {(cx, cy)}"
