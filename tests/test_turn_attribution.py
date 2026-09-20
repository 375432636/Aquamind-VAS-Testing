from voice_scenarios.report import evaluate
from voice_scenarios.turn_attribution import attribute_reused_listen_turns


def _event(seq, listen_turn_id, event, *, data=None, span=None, parent=None):
    return {
        "seq": seq,
        "listen_turn_id": listen_turn_id,
        "event": event,
        "data": data or {},
        "span_id": span,
        "parent_span_id": parent,
        "output_id": None,
    }


def test_continuous_vad_diagnostics_follow_each_logical_report_turn():
    report = {
        "turns": [
            {"server_listen_turn_id": 1},
            {"server_listen_turn_id": 2},
            {"server_listen_turn_id": 2},
        ]
    }
    events = [
        _event(1, None, "session_started"),
        _event(2, 1, "speech_chunk_started", data={"utterance_id": "ptt"}),
        _event(10, 2, "listen_start_received"),
        _event(11, 2, "speech_chunk_started", data={"utterance_id": "vad-a"}),
        _event(12, 2, "asr_final"),
        _event(13, 2, "llm_request_started", data={"utterance_id": "vad-a"}, span="a"),
        _event(20, 2, "speech_chunk_started", data={"utterance_id": "vad-b"}),
        _event(21, 2, "asr_final"),
        _event(22, 2, "llm_request_finished", parent="a"),
    ]

    attributed, failures = attribute_reused_listen_turns(report, events)

    assert failures == []
    assert [row["client_turn_index"] for row in attributed] == [
        0,
        1,
        2,
        2,
        2,
        2,
        3,
        3,
        2,
    ]


def test_continuous_vad_reports_an_explicit_boundary_mismatch():
    report = {
        "turns": [
            {"server_listen_turn_id": 1},
            {"server_listen_turn_id": 1},
        ]
    }
    events = [_event(1, 1, "speech_chunk_started", data={"utterance_id": "only-one"})]

    _, failures = attribute_reused_listen_turns(report, events)

    assert len(failures) == 1
    assert "1 个语句边界" in failures[0]
    assert "2 个轮次" in failures[0]


def test_report_gives_each_continuous_vad_turn_only_its_server_timeline():
    result = {
        "name": "连续 VAD",
        "status": "passed",
        "turns": [
            {
                "id": "turn-001",
                "status": "completed",
                "events": [],
                "server_listen_turn_id": 1,
            },
            {
                "id": "turn-002",
                "status": "completed",
                "events": [],
                "server_listen_turn_id": 1,
            },
        ],
    }
    events = [
        {
            **_event(
                1,
                1,
                "speech_chunk_started",
                data={"utterance_id": "first"},
            ),
            "clock_id": "vas",
            "monotonic_ns": 1,
        },
        {
            **_event(2, 1, "asr_final"),
            "clock_id": "vas",
            "monotonic_ns": 2,
        },
        {
            **_event(
                3,
                1,
                "speech_chunk_started",
                data={"utterance_id": "second"},
            ),
            "clock_id": "vas",
            "monotonic_ns": 3,
        },
        {
            **_event(4, 1, "asr_final"),
            "clock_id": "vas",
            "monotonic_ns": 4,
        },
    ]

    report = evaluate(result, events)

    assert [[row["seq"] for row in turn["vas_events"]] for turn in report["turns"]] == [
        [1, 2],
        [3, 4],
    ]
