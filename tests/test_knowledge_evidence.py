from voice_scenarios.timeline import group_timeline_spans, request_spans


def event(name, data=None, *, span="rag-1", clock="vas", at=1):
    return dict(
        event=name,
        data=data or {},
        span_id=span,
        clock_id=clock,
        monotonic_ns=at * 1_000_000_000,
        listen_turn_id=1,
        status="ok",
    )


def call(text="Oracube", *, span="rag-1", clock="vas", status="returned"):
    rows = [
        event(
            "tool_call_started",
            {"category": "knowledge_base", "tool_name": "rag-lightrag_search"},
            span=span,
            clock=clock,
        )
    ]
    rows += [
        event(
            "knowledge_lookup_result",
            {"retrieval_status": status},
            span=span,
            clock=clock,
        )
    ]
    for kind, value in (("query", "水晶球"), ("returned", text)):
        if value:
            rows.append(
                event(
                    "knowledge_context_part",
                    {"content_kind": kind, "part_index": 0, "text": value},
                    span=span,
                    clock=clock,
                )
            )
        rows.append(
            event(
                "knowledge_context_complete",
                {
                    "content_kind": kind,
                    "part_count": int(bool(value)),
                    "available": True,
                    "captured_bytes": len(value.encode()),
                    "truncated": False,
                },
                span=span,
                clock=clock,
            )
        )
    return rows + [event("tool_call_finished", span=span, clock=clock, at=2)]


def test_rag_details_attached_per_request_and_existing_spans_can_be_regenerated():
    rows = call() + call("另一条结果", span="rag-2")
    spans, errors = request_spans(rows)
    assert not errors
    assert spans[0]["knowledge_evidence"]["returned"]["text"] == "Oracube"
    assert spans[1]["knowledge_evidence"]["returned"]["text"] == "另一条结果"
    for span in spans:
        span.pop("knowledge_evidence")
    lanes = group_timeline_spans(spans, rows)
    assert lanes[0]["segments"][0]["knowledge_evidence"]["query"]["text"] == "水晶球"


def test_same_span_id_from_different_clocks_does_not_mix_results():
    from voice_scenarios.knowledge_evidence import knowledge_evidence

    evidence = knowledge_evidence(call("A") + call("B", clock="other"))
    assert evidence["vas", "rag-1"]["returned"]["text"] == "A"
    assert evidence["other", "rag-1"]["returned"]["text"] == "B"


def test_old_reports_are_unrecorded_and_empty_reply_is_not_no_search_hits():
    rows = [e for e in call() if e["event"].startswith("tool_call")]
    spans, _ = request_spans(rows)
    assert spans[0]["knowledge_evidence"]["label"] == "结果未采集"
    spans, _ = request_spans(call("", status="empty"))
    evidence = spans[0]["knowledge_evidence"]
    assert evidence["label"] == "空返回"
    assert evidence["returned"]["complete"] and evidence["returned"]["available"]


def test_missing_and_conflicting_fragments_never_claim_complete():
    from voice_scenarios.knowledge_evidence import knowledge_evidence

    rows = call()
    rows.append(
        event(
            "knowledge_context_part",
            {"content_kind": "returned", "part_index": 0, "text": "DIFFERENT"},
        )
    )
    assert not knowledge_evidence(rows)["vas", "rag-1"]["returned"]["complete"]
    rows = [
        e
        for e in call()
        if not (
            e["event"] == "knowledge_context_part"
            and e["data"]["content_kind"] == "returned"
        )
    ]
    assert not knowledge_evidence(rows)["vas", "rag-1"]["returned"]["complete"]


def test_duplicate_delivery_is_safe_but_truncation_is_visible():
    from voice_scenarios.knowledge_evidence import knowledge_evidence

    rows = call()
    rows += [e for e in rows if e["event"] == "knowledge_context_part"]
    for e in rows:
        if e["event"] == "knowledge_context_complete":
            e["data"]["truncated"] = True
    evidence = knowledge_evidence(rows)["vas", "rag-1"]
    assert evidence["returned"]["text"] == "Oracube"
    assert evidence["returned"]["complete"] and evidence["returned"]["truncated"]


def test_other_tools_do_not_get_knowledge_details():
    rows = [
        event("tool_call_started", {"category": "other", "tool_name": "weather"}),
        event("tool_call_finished", at=2),
    ]
    spans, _ = request_spans(rows)
    assert "knowledge_evidence" not in spans[0]


def test_evidence_survives_turn_and_session_clock_projection():
    from voice_scenarios.clock_timeline import combined_timeline, session_trace_timeline

    rows = call()
    for e in rows:
        e["wall_time_ns"] = 1_800_000_000_000_000_000 + e["monotonic_ns"]
    spans, _ = request_spans(rows)
    turn = dict(
        id="one",
        vas_events=rows,
        events=[],
        spans=spans,
        timeline_lanes=group_timeline_spans(spans, rows),
    )
    clock = dict(monotonic_ns=0, wall_time_ns=1_800_000_000_000_000_000)
    timeline = combined_timeline(turn, clock)
    assert "Oracube" in str(timeline)
    turn["combined_timeline"] = timeline
    report = dict(turns=[turn], client_clock=clock, session_playback={"zero_at_ns": 0})
    assert "Oracube" in str(session_trace_timeline(report))
