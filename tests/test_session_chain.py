import copy

import pytest

from voice_scenarios.session_chain import build_session_chain


def recording():
    turns = []
    for index in range(1, 4):
        client, server = index * 10, index * 10 + 90
        turns.append(
            {
                "listen_turn_id": index,
                "events": [
                    {
                        "event": "listen_start_sent",
                        "at_ns": int(client * 1e9),
                        "data": {"listen_turn_id": index},
                    },
                    {
                        "event": "audio_packet_received",
                        "at_ns": int((client + 1.2) * 1e9),
                        "data": {"audio_seq": index},
                    },
                ],
                "vas_events": [
                    {
                        "event": event,
                        "monotonic_ns": int((server + at) * 1e9),
                        "clock_id": "server",
                        "listen_turn_id": index,
                        "span_id": f"s{index}" if event.startswith("llm") else None,
                        "data": {"audio_seq": index},
                    }
                    for event, at in [
                        ("listen_start_received", 0.1),
                        ("llm_request_started", 0.2),
                        ("llm_request_finished", 0.8),
                        ("audio_output_frame", 1),
                    ]
                ],
            }
        )
    return {"session_playback": {"zero_at_ns": 10_000_000_000}, "turns": turns}


def test_all_turns_share_one_offset_and_keep_inter_turn_gaps():
    source = recording()
    before = copy.deepcopy(source)
    chain = build_session_chain(source)
    assert source == before
    assert chain["alignment"]["status"] == "bounded"
    assert chain["alignment"]["uncertainty_ms"] == pytest.approx(150)
    spans = chain["lanes"][0]["segments"]
    assert [s["turn_index"] for s in spans] == [1, 2, 3]
    assert [s["start_seconds"] for s in spans] == pytest.approx([0.25, 10.25, 20.25])
    assert [s["duration_seconds"] for s in spans] == pytest.approx([0.6] * 3)
    assert chain["client_lanes"][0]["segments"][2]["start_seconds"] == pytest.approx(
        21.2
    )


def test_conflicting_clock_bounds_never_fabricate_alignment():
    source = recording()
    source["turns"][0]["events"][1]["at_ns"] = 1
    chain = build_session_chain(source)
    assert chain["status"] == "unavailable"
    assert not chain["lanes"]
    assert chain["client_lanes"]


def test_greeting_and_unfinished_spans_are_visible_without_inventing_duration():
    source = recording()
    source["session_vas_events"] = [
        {
            "event": "tts_request_started",
            "monotonic_ns": 100_000_000_000,
            "clock_id": "server",
            "span_id": "greeting",
            "listen_turn_id": None,
            "data": {},
        }
    ]
    chain = build_session_chain(source)
    greeting = chain["lanes"][1]["segments"][0]
    assert greeting["turn_index"] == 0
    assert greeting["status"] == "unfinished"
    assert greeting["duration_seconds"] is None


def test_missing_clock_origin_is_explicit():
    assert build_session_chain({})["status"] == "unavailable"
