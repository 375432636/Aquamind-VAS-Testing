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


def test_milestones_use_request_and_segment_ids_and_keep_embedding_overlap():
    spans = [
        span("llm_request", "llm", 0, 9_000_000_000),
        span("llm_request", "other", 900_000_000, 8_000_000_000),
        span("tts_request", "tts", 2_000_000_000, 5_000_000_000, "llm"),
        span("guardrail_embedding", "guard", 0, 1_500_000_000),
    ]
    spans[2]["data"]["segment_id"] = "speech-1"

    def event(name, sid, at, **data):
        return dict(event=name, span_id=sid, monotonic_ns=int(at * 1e9), data=data)

    events = [
        event("llm_first_token", "llm", 1, delta_kind="tool"),
        event("tts_segment_ready", "llm", 1.9, segment_id="speech-1", punctuation="，"),
        event("http_connection_reused", "tts", 2.01, http_request_id="http-1"),
        event("tts_first_pcm", "tts", 2.5),
        event("guardrail_released", None, 1.6),
    ]
    lanes = group_timeline_spans(spans, events)
    llm = next(l for l in lanes if l["label"] == "LLM #1 · 未采集")
    tts = next(l for l in lanes if l["category"] == "tts_request")
    guard = next(l for l in lanes if l["category"] == "guardrail_embedding")
    assert [m["event"] for m in llm["markers"]] == ["llm_first_token"]
    assert [m["event"] for m in tts["markers"]] == [
        "tts_segment_ready",
        "http_connection_reused",
        "tts_first_pcm",
    ]
    assert tts["markers"][-1]["since_request_seconds"] == 0.5
    assert guard["segments"][0]["end_ns"] == 1_500_000_000
    assert guard["markers"][0]["event"] == "guardrail_released"


def test_cross_clock_marker_is_not_given_a_false_duration():
    request = span("llm_request", "llm", 1, 10)
    request["clock_id"] = "vas-a"
    events = [
        dict(
            event="llm_first_token",
            span_id="llm",
            clock_id="vas-b",
            monotonic_ns=9,
            data={},
        )
    ]
    lane = group_timeline_spans([request], events)[0]
    assert lane["markers"] == []


def test_text_completion_joins_only_an_unambiguous_matching_output_lane():
    spans = [span("tts_request", "a", 0, 10), span("tts_request", "b", 11, 20)]
    events = [
        dict(event="tts_request_started", span_id=sid, output_id=oid)
        for sid, oid in (("a", "answer-1"), ("b", "answer-2"))
    ]
    events.append(
        dict(
            event="tts_text_complete",
            span_id=None,
            output_id="answer-1",
            monotonic_ns=21,
        )
    )
    lanes = group_timeline_spans(spans, events)
    assert len(lanes) == 2
    assert lanes[0]["markers"][0]["event"] == "tts_text_complete"
    assert lanes[1]["markers"] == []


def test_nonstream_asr_is_not_labelled_as_upload_time():
    request = span("asr_request", "local", 0, 10)
    request["data"]["mode"] = "NON_STREAM"
    assert group_timeline_spans([request], [])[0]["label"] == "ASR 识别请求"
