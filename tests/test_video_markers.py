import copy
import json
import re

from voice_scenarios.report import build_report


def test_display_video_and_images_share_receive_time_and_keep_repeated_items(tmp_path):
    message = {
        "type": "display",
        "session_id": "video-session",
        "items": [
            {
                "kind": "video",
                "url": "https://example.com/demo.mp4",
                "sha256": "a" * 64,
            },
            {"kind": "image", "url": "https://example.com/poster.png"},
            {
                "kind": "video",
                "url": "https://example.com/demo.mp4",
                "sha256": "a" * 64,
            },
        ],
    }
    report = {
        "name": "视频时序",
        "status": "passed",
        "turns": [
            {
                "id": "one",
                "status": "completed",
                "events": [
                    {"event": "display", "at_ns": 12_000_000_000, "data": message}
                ],
            },
            {"id": "two", "status": "completed", "events": []},
        ],
        "session_playback": {"zero_at_ns": 10_000_000_000, "duration_seconds": 10},
    }
    original = copy.deepcopy(report)
    build_report(report, tmp_path / "report.html")

    def payload(name):
        return json.loads(
            re.search(
                r'<script id="data" type="application/json">(.*?)</script>',
                (tmp_path / name).read_text(),
                re.S,
            )[1]
        )

    overview = payload("report.html")
    first = payload("turn-001.html")["turn"]
    markers = overview["session_playback"]["media_markers"]
    assert [m["kind"] for m in markers] == ["video", "image", "video"]
    assert [m["label"] for m in markers] == [
        "视频 #1 到达",
        "图片 #1 到达",
        "视频 #2 到达",
    ]
    assert [m["at_seconds"] for m in markers] == [2, 2, 2]
    assert [m["turn_index"] for m in markers] == [1, 1, 1]
    assert first["media_markers"][0]["start_ns"] == 12_000_000_000
    assert first["media_markers"][0]["url"] == message["items"][0]["url"]
    assert first["timeline_lanes"] == []
    assert payload("turn-002.html")["turn"]["media_markers"] == []
    assert "视频到达" in (tmp_path / "report.html").read_text()
    assert report == original


def test_invalid_display_payloads_do_not_break_markers_or_hide_missing_urls():
    from voice_scenarios.timeline import media_timeline_markers

    rows = [None, {"items": None}, {"items": "bad"}, {"items": [None, 2, {}]}]
    rows.append({"items": [{"kind": "audio"}, {"kind": "video", "url": 42}]})
    markers = media_timeline_markers(
        [{"event": "display", "at_ns": 1_000_000_000, "data": data} for data in rows]
    )
    assert len(markers) == 1
    assert markers[0]["kind"] == "video"
    assert markers[0]["url"] is None
