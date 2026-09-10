from voice_scenarios.timeline import group_timeline_spans


def span(name, span_id, start, end, parent=None):
    return dict(
        name=name,
        span_id=span_id,
        start_ns=start,
        end_ns=end,
        parent_span_id=parent,
        data={},
        status="ok",
    )


def test_groups_by_llm_reply_without_merging_gaps_or_overlaps():
    spans = [
        span("llm_request", "llm-a", 0, 20),
        span("llm_request", "llm-b", 21, 40),
        span("tts_request", "a-1", 10, 25, "llm-a"),
        span("tts_request", "b-1", 30, 45, "llm-b"),
        span("tts_request", "a-2", 35, 50, "llm-a"),
        span("tts_request", "a-3", 48, 60, "llm-a"),
    ]
    lanes = group_timeline_spans(spans, [])
    tts = [lane for lane in lanes if lane["category"] == "tts_request"]
    assert len(tts) == 2
    assert [s["span_id"] for s in tts[0]["segments"]] == ["a-1", "a-2", "a-3"]
    assert [(s["start_ns"], s["end_ns"]) for s in tts[0]["segments"]] == [
        (10, 25),
        (35, 50),
        (48, 60),
    ]
    assert [s["span_id"] for s in tts[1]["segments"]] == ["b-1"]
    assert spans[2]["data"] == {}


def test_unlinked_tts_uses_explicit_output_id_and_never_nearest_llm():
    spans = [
        span("llm_request", "llm", 0, 50),
        span("tts_request", "pre-1", 4, 8),
        span("tts_request", "answer", 9, 11),
        span("tts_request", "pre-2", 12, 15),
        span("tts_request", "unknown-1", 16, 18),
        span("tts_request", "unknown-2", 19, 21),
    ]
    events = [
        dict(
            event="tts_request_started",
            span_id=sid,
            output_id=oid,
            output_kind=kind,
            parent_span_id=None,
        )
        for sid, oid, kind in [
            ("pre-1", "pre", "pre_speech"),
            ("answer", "answer", "answer"),
            ("pre-2", "pre", "pre_speech"),
        ]
    ]
    lanes = group_timeline_spans(spans, events)
    tts = [lane for lane in lanes if lane["category"] == "tts_request"]
    assert len(tts) == 4
    assert [s["span_id"] for s in tts[0]["segments"]] == ["pre-1", "pre-2"]
    assert all(lane["llm_span_id"] is None for lane in tts)
    assert tts[0]["segments"][0]["output_kind"] == "pre_speech"
