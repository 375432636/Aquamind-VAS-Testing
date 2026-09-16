import copy

from voice_scenarios.clock_timeline import session_trace_timeline
from voice_scenarios.report import build_report, evaluate
from voice_scenarios.timeline import group_timeline_spans


def recorded_sentence(turn=1):
    def event(name, at, **data):
        return dict(
            event=name,
            span_id="tts",
            monotonic_ns=at,
            clock_id="vas",
            seq=at,
            server_session_id="session",
            listen_turn_id=turn,
            output_id="answer",
            data=data,
        )

    vas = [
        event("tts_segment_ready", 100, segment_id="segment", text_chars=4),
        event("tts_request_started", 101, segment_id="segment"),
        event("tts_state_sent", 110, state="sentence_start"),
        event("audio_output_started", 111, audio_seq=9),
        event("tts_request_finished", 120),
    ]
    client = [
        dict(
            event="tts_sentence_start",
            at_ns=10,
            data=dict(
                text="第一句。", session_id="session", response_listen_turn_id=turn
            ),
        ),
        dict(event="audio_packet_received", at_ns=11, data=dict(audio_seq=1)),
    ]
    return vas, client


def test_report_links_text_before_legacy_audio_sequence_enrichment(tmp_path):
    vas, client = recorded_sentence()
    # Old browsers used different sequence counters. Derived ownership from this
    # earlier edge must not overwrite actual sentence-message evidence.
    vas.insert(
        0,
        dict(
            event="audio_output_started",
            monotonic_ns=1,
            clock_id="vas",
            listen_turn_id=None,
            output_id="welcome",
            data=dict(audio_seq=1),
        ),
    )
    result = dict(
        name="test",
        status="passed",
        turns=[dict(id="one", status="completed", events=client)],
    )
    original = copy.deepcopy(result)
    report = evaluate(result, vas)
    span = report["turns"][0]["spans"][0]
    assert span["tts_evidence"]["text"] == "第一句。"
    assert span["tts_evidence"]["source"] == "sentence_protocol_order"
    assert result == original
    lane = group_timeline_spans(
        report["turns"][0]["spans"], report["turns"][0]["vas_events"]
    )[0]
    marker = next(m for m in lane["markers"] if m["event"] == "tts_segment_ready")
    assert marker["label"] == "TTS 分段就绪（消费侧）"
    assert marker["segment_number"] == 1
    assert marker["span_id"] == span["span_id"]
    assert marker["start_ns"] == 100
    assert marker["tts_evidence"]["text"] == "第一句。"
    build_report(report, tmp_path / "report.html")
    for filename in ("report.html", "turn-001.html"):
        page = (tmp_path / filename).read_text()
        assert '"text": "第一句。"' in page
        assert "tts-text-bar" in page


def test_startup_sentence_is_available_in_the_overview():
    vas, client = recorded_sentence(None)
    chart = session_trace_timeline(
        dict(session_events=vas, startup=dict(events=client), turns=[])
    )
    span = chart["lanes"][0]["segments"][0]
    assert span["tts_evidence"]["text"] == "第一句。"
    assert span["turn_index"] is None
