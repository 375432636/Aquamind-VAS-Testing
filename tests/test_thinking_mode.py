import json
import re

import pytest

from voice_scenarios.report import build_report, evaluate
from voice_scenarios.timeline import group_timeline_spans, request_spans


def events_for_turn(index, mode):
    base = index * 20_000_000_000
    params = {} if mode is None else {"enable_thinking": mode}
    rows = [
        ("asr_request_started", "asr", 0, {}),
        ("asr_partial", "asr", 1, {"text_chars": 0}),
        ("asr_partial", "asr", 2, {"text_chars": 1}),
        ("asr_partial", "asr", 3, {"text_chars": 4}),
        ("asr_final", "asr", 4, {}),
        ("asr_request_finished", "asr", 5, {}),
        (
            "llm_request_started",
            "llm",
            6,
            {"model": "same-model", "parameters": params},
        ),
        ("llm_first_token", "llm", 7, {"delta_kind": "text"}),
        ("llm_request_finished", "llm", 8, {}),
        ("tts_request_started", "tts", 7, {}),
        ("tts_first_pcm", "tts", 8, {}),
        ("tts_request_finished", "tts", 9, {}),
    ]
    return [
        dict(
            event=name,
            span_id=f"{sid}-{index}",
            listen_turn_id=index,
            monotonic_ns=base + offset * 100_000_000,
            clock_id="vas",
            data=data,
        )
        for name, sid, offset, data in rows
    ]


@pytest.mark.parametrize(
    "mode,label",
    [
        (True, "Thinking"),
        (False, "Unthinking"),
        (None, "未采集"),
        ("false", "未采集"),
        (0, "未采集"),
    ],
)
def test_llm_mode_is_explicit_config_not_model_name(mode, label):
    events = events_for_turn(1, mode)
    spans, _ = request_spans(events)
    lanes = group_timeline_spans(spans, events)
    llm = next(l for l in lanes if l["category"] == "llm_request")
    assert llm["thinking_mode"] == label
    assert label in llm["label"]
    assert all(m["thinking_mode"] == label for m in llm["markers"])


def test_asr_first_character_skips_empty_updates():
    events = events_for_turn(1, False)
    spans, _ = request_spans(events)
    lanes = group_timeline_spans(spans, events)
    partials = [m for l in lanes for m in l["markers"] if m["event"] == "asr_partial"]
    assert len(partials) == 1
    assert partials[0]["data"]["text_chars"] == 1
    assert partials[0]["since_request_seconds"] == pytest.approx(0.2)
    assert "首字" in partials[0]["label"]


def test_static_overview_and_details_share_all_markers_and_modes(tmp_path):
    source = {
        "name": "three turns",
        "status": "passed",
        "turns": [
            {"id": f"t-{i}", "status": "completed", "events": [], "input_text": "test"}
            for i in range(1, 4)
        ],
        "session_playback": {
            "status": "ready",
            "path": "session.mixed.wav",
            "duration_seconds": 70,
            "zero_at_ns": 0,
        },
    }
    events = [
        event
        for i, mode in enumerate((True, False, None), 1)
        for event in events_for_turn(i, mode)
    ]
    report = evaluate(source, events)
    build_report(report, tmp_path / "report.html")

    def data(name):
        return json.loads(
            re.search(
                r'<script id="data" type="application/json">(.*?)</script>',
                (tmp_path / name).read_text(),
            )[1]
        )

    overview = data("report.html")["session_playback"]["vas_timeline"]["lanes"]
    for index, mode in enumerate(("Thinking", "Unthinking", "未采集"), 1):
        lanes = [l for l in overview if l["turn_index"] == index]
        detail = data(f"turn-{index:03d}.html")["turn"]
        server = [
            l for l in detail["combined_timeline"]["lanes"] if l["source"] == "server"
        ]

        def milestones(items):
            return [
                (m["event"], m["start_ns"], m["label"], m.get("thinking_mode"))
                for l in items
                for m in l["markers"]
            ]

        assert milestones(lanes) == milestones(server)
        assert {m[0] for m in milestones(lanes)} >= {
            "asr_request_started",
            "asr_partial",
            "asr_final",
            "llm_first_token",
            "tts_first_pcm",
        }
        assert (
            next(l for l in lanes if l["category"] == "llm_request")["thinking_mode"]
            == mode
        )
        assert detail["llm_requests"][0]["thinking_mode"] == mode
        assert (
            mode
            in (tmp_path / f"turn-{index:03d}.html")
            .read_text()
            .split('<script id="data"')[0]
        )


def test_cancelled_llm_still_labels_its_observed_mode():
    events = [
        e for e in events_for_turn(1, False) if e["event"] != "llm_request_finished"
    ]
    spans, _ = request_spans(events)
    lanes = group_timeline_spans(spans, events)
    token = next(
        m for l in lanes for m in l["markers"] if m["event"] == "llm_first_token"
    )
    assert token["thinking_mode"] == "Unthinking"
