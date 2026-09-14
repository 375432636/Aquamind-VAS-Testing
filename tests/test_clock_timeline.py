import copy
import json
import re

from voice_scenarios.clock_timeline import combined_timeline, session_trace_timeline
from voice_scenarios.report import build_report, evaluate
from voice_scenarios.timeline import group_timeline_spans, media_timeline_markers

SECOND = 1_000_000_000
# 2026-09-14 16:00:00 in Beijing, 08:00:00 UTC.
EPOCH = 1789372800 * SECOND


def example():
    events = [
        {"event": "speech_input_finished", "at_ns": 10 * SECOND, "data": {}},
        {
            "event": "display",
            "at_ns": 12 * SECOND,
            "data": {"items": [{"kind": "video", "url": "https://example.com/v.mp4"}]},
        },
    ]
    vas = [
        {
            "event": name,
            "span_id": "llm",
            "clock_id": "vas-1",
            "monotonic_ns": (100 + offset) * SECOND,
            "wall_time_ns": EPOCH + (offset + 3) * SECOND,
            "data": {},
        }
        for name, offset in [
            ("llm_request_started", 0),
            ("llm_first_token", 1),
            ("llm_request_finished", 2),
        ]
    ]
    spans = [
        {
            "name": "llm_request",
            "span_id": "llm",
            "clock_id": "vas-1",
            "start_ns": 100 * SECOND,
            "end_ns": 102 * SECOND,
            "duration_ms": 2000,
        }
    ]
    turn = {
        "events": events,
        "vas_events": vas,
        "timeline_lanes": group_timeline_spans(spans, vas),
        "media_markers": media_timeline_markers(events),
    }
    clock = {"monotonic_ns": 10 * SECOND, "wall_time_ns": EPOCH}
    return turn, clock


def test_one_axis_uses_recorded_wall_times_without_cross_clock_offset_correction():
    turn, clock = example()
    original = copy.deepcopy(turn)
    chart = combined_timeline(turn, clock)
    assert chart["mode"] == "wall"
    assert chart["time_zone"] == "Asia/Shanghai"
    assert chart["origin_wall_time_ms"] == EPOCH // 1_000_000
    assert {lane["source"] for lane in chart["lanes"]} == {"client", "server"}
    server = next(lane for lane in chart["lanes"] if lane["source"] == "server")
    assert server["segments"][0]["plot_start_ns"] == 3 * SECOND
    assert server["segments"][0]["plot_end_ns"] == 5 * SECOND
    token = next(m for m in server["markers"] if m["event"] == "llm_first_token")
    assert token["plot_start_ns"] == 4 * SECOND
    assert token["since_request_seconds"] == 1
    media = next(lane for lane in chart["lanes"] if lane["category"] == "media")
    assert media["markers"][0]["plot_start_ns"] == 2 * SECOND
    assert turn == original  # Reporting must not rewrite raw timing / latency evidence.


def test_legacy_recording_keeps_relative_clocks_and_does_not_invent_client_wall_time():
    turn, _ = example()
    chart = combined_timeline(turn, None)
    assert chart["mode"] == "relative"
    assert chart["origin_wall_time_ms"] is None
    client = next(lane for lane in chart["lanes"] if lane["source"] == "client")
    server = next(lane for lane in chart["lanes"] if lane["source"] == "server")
    assert client["markers"][0]["plot_start_ns"] == 0
    assert client["markers"][0]["wall_time_ms"] is None
    assert server["segments"][0]["plot_start_ns"] == 0
    assert server["segments"][0]["wall_time_ms"] == EPOCH // 1_000_000 + 3000


def test_missing_server_clock_or_clock_jump_is_explicit_and_keeps_monotonic_duration():
    turn, clock = example()
    # A wall-clock adjustment must not turn a two-second LLM request into -7 s.
    turn["vas_events"][-1]["wall_time_ns"] -= 9 * SECOND
    chart = combined_timeline(turn, clock)
    assert chart["mode"] == "relative"
    assert chart["reason"] == "clock_discontinuity"
    server = next(lane for lane in chart["lanes"] if lane["source"] == "server")
    assert server["segments"][0]["plot_end_ns"] == 2 * SECOND
    assert server["segments"][0]["duration_ms"] == 2000
    turn, clock = example()
    turn["vas_events"][0]["clock_id"] = "another-process"
    assert combined_timeline(turn, clock)["mode"] == "relative"


def test_wall_timing_works_with_diagnostics_off_and_without_client_events():
    turn, clock = example()
    turn["timeline_lanes"] = []
    assert combined_timeline(turn, clock)["mode"] == "wall"
    turn, clock = example()
    turn["events"] = []
    turn["media_markers"] = []
    assert combined_timeline(turn, None)["mode"] == "wall"


def session_example():
    first, clock = example()
    second = copy.deepcopy(first)
    for event in second["vas_events"]:
        event["monotonic_ns"] += 20 * SECOND
        event["wall_time_ns"] += 20 * SECOND
    for lane in second["timeline_lanes"]:
        for span in lane["segments"]:
            span["start_ns"] += 20 * SECOND
            span["end_ns"] += 20 * SECOND
        for marker in lane["markers"]:
            marker["start_ns"] += 20 * SECOND
    return {
        "client_clock": clock,
        "turns": [first, second],
        "session_playback": {"zero_at_ns": 10 * SECOND, "duration_seconds": 24},
    }


def test_session_trace_preserves_multi_turn_positions_and_detail_dimensions():
    report = session_example()
    original = copy.deepcopy(report)
    chart = session_trace_timeline(report)
    assert chart["mode"] == "wall"
    assert chart["origin_wall_time_ms"] == EPOCH / 1e6
    assert chart["axis_start_seconds"] == 0
    assert chart["axis_end_seconds"] == 25  # VAS is not clipped to 24 s of audio.
    assert [lane["turn_index"] for lane in chart["lanes"]] == [1, 2]
    assert [lane["segments"][0]["plot_start_ns"] for lane in chart["lanes"]] == [
        3 * SECOND,
        23 * SECOND,
    ]
    for detail, lane in zip(report["turns"], chart["lanes"]):
        assert lane["label"] == detail["timeline_lanes"][0]["label"]
        assert lane["markers"][1]["since_request_seconds"] == 1
    assert report == original


def test_old_session_does_not_align_vas_to_client_turn_starts():
    report = session_example()
    del report["client_clock"]
    chart = session_trace_timeline(report)
    assert chart["mode"] == "relative"
    # One zero for the whole VAS process, not a new zero for each turn.
    assert [lane["segments"][0]["plot_start_ns"] for lane in chart["lanes"]] == [
        0,
        20 * SECOND,
    ]
    assert chart["origin_wall_time_ms"] is None


def test_session_level_spans_before_playback_and_orphan_markers_are_retained():
    report = session_example()
    greeting = copy.deepcopy(report["turns"][0]["vas_events"])
    for event in greeting:
        event["span_id"] = "greeting"
        event["monotonic_ns"] -= 5 * SECOND
        event["wall_time_ns"] -= 5 * SECOND
    report["session_events"] = greeting
    chart = session_trace_timeline(report)
    assert chart["axis_start_seconds"] == -2
    assert chart["lanes"][0]["turn_index"] is None
    assert chart["lanes"][0]["segments"][0]["plot_start_ns"] == -2 * SECOND
    assert chart["lanes"][1]["segments"][0]["plot_start_ns"] == 3 * SECOND


def test_empty_trace_leaves_playback_extent_unchanged():
    chart = session_trace_timeline(
        {"turns": [], "session_playback": {"duration_seconds": 8}}
    )
    assert chart["lanes"] == []
    assert chart["axis_start_seconds"] == 0
    assert chart["axis_end_seconds"] == 8


def test_overview_contains_all_turn_traces_but_detail_keeps_only_its_own(tmp_path):
    source = session_example()
    source.update(name="连续两轮", status="passed")
    source["session_playback"].update(status="ready", path="session.mixed.wav")
    vas_events = []
    for index, turn in enumerate(source["turns"], 1):
        turn.update(id=f"turn-{index}", input_text=f"问题 {index}", status="completed")
        for event in turn["vas_events"]:
            event["listen_turn_id"] = index
            event["span_id"] = f"request-{index}"
            event["data"].update(provider="OpenAI", model=f"model-{index}")
            vas_events.append(event)
    report = evaluate(source, vas_events)
    original = copy.deepcopy(report)
    build_report(report, tmp_path / "report.html")

    def page_data(name):
        html = (tmp_path / name).read_text()
        return html, json.loads(
            re.search(
                r'<script id="data" type="application/json">(.*?)</script>', html
            )[1]
        )

    overview, data = page_data("report.html")
    trace = data["session_playback"]["vas_timeline"]
    assert overview.count("<audio ") == 1
    assert "会话全链路时序" in overview
    assert trace["mode"] == "wall"
    assert [lane["turn_index"] for lane in trace["lanes"]] == [1, 2]
    assert [lane["segments"][0]["plot_start_ns"] for lane in trace["lanes"]] == [
        3 * SECOND,
        23 * SECOND,
    ]
    for index in (1, 2):
        html, detail = page_data(f"turn-{index:03d}.html")
        assert "vas_timeline" not in detail["session_playback"]
        assert "<audio " not in html
        server = next(
            lane
            for lane in detail["turn"]["combined_timeline"]["lanes"]
            if lane["source"] == "server"
        )
        overview_lane = trace["lanes"][index - 1]
        assert overview_lane["segments"][0]["data"] == server["segments"][0]["data"]
        assert overview_lane["segments"][0]["data"]["model"] == f"model-{index}"
        assert [m["event"] for m in overview_lane["markers"]] == [
            m["event"] for m in server["markers"]
        ]
        assert f"model-{3-index}" not in html
    assert report == original
