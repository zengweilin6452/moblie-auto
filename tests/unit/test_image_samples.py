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
