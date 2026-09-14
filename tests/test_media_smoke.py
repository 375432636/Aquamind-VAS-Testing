import json

import pytest

from voice_scenarios.ci_run import validate_turns
from voice_scenarios.report import evaluate


def test_media_smoke_checks_actual_items_per_turn_even_without_server_diagnostics():
    expected = {"image_items_min": 1, "video_items_min": 1}
    validate_turns(
        json.dumps([{"text": "展示图片和视频", "expect": expected}]),
        {"diagnostics": "off"},
    )
    message = {
        "type": "display",
        "items": [
            {"kind": "image", "url": "https://example.com/a.png"},
            {"kind": "video", "url": "https://example.com/a.mp4"},
            {"kind": "video", "url": "https://example.com/a.mp4"},
        ],
    }
    report = evaluate(
        {
            "name": "媒体检查",
            "status": "passed",
            "turns": [
                {
                    "id": "one",
                    "status": "completed",
                    "expected": expected,
                    "events": [
                        {"event": "display", "at_ns": 123, "data": message},
                    ],
                },
                {
                    "id": "two",
                    "status": "completed",
                    "expected": expected,
                    "events": [],
                },
            ],
        },
        [],
    )
    assert report["turns"][0]["metrics"]["image_items"] == 1
    assert report["turns"][0]["metrics"]["video_items"] == 2
    assert all(c["passed"] for c in report["turns"][0]["checks"])
    assert not any(c["passed"] for c in report["turns"][1]["checks"])
    assert report["turns"][1]["metrics"]["video_items"] == 0
    assert report["status"] == "failed"


@pytest.mark.parametrize("value", [True, -1, "1", float("nan")])
def test_media_count_rejects_invalid_thresholds(value):
    with pytest.raises(ValueError):
        validate_turns(
            json.dumps([{"text": "视频", "expect": {"video_items_min": value}}]),
            {"diagnostics": "frame"},
        )
