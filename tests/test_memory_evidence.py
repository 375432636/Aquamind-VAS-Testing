from voice_scenarios.memory_evidence import candidate_decisions, memory_evidence
from voice_scenarios.timeline import group_timeline_spans, request_spans


def event(name, data, *, span="m", at=1):
    return dict(
        event=name,
        data=data,
        span_id=span,
        clock_id="vas",
        listen_turn_id=1,
        monotonic_ns=at * 1000000000,
        status="ok",
    )


def test_returned_and_injected_are_distinct_and_fragments_reassemble():
    rows = [
        event(
            "memory_lookup_result",
            dict(memory_lookup_id="lookup", retrieval_status="hit", adopted=True),
        )
    ]
    for kind, text in (("returned", "喜欢茶；生日"), ("injected", "生日")):
        rows.extend(
            [
                event(
                    "memory_context_part",
                    dict(
                        memory_lookup_id="lookup",
                        content_kind=kind,
                        part_index=0,
                        text=text,
                    ),
                ),
                event(
                    "memory_context_complete",
                    dict(
                        memory_lookup_id="lookup",
                        content_kind=kind,
                        part_count=1,
                        truncated=False,
                    ),
                ),
            ]
        )
    result = memory_evidence(rows)["lookup"]
    assert result["label"] == "命中 · 已采用"
    assert result["returned"]["text"] == "喜欢茶；生日"
    assert result["injected"]["text"] == "生日"


def test_missing_fragments_and_old_reports_are_never_claimed_empty():
    assert memory_evidence([]) == {}
    rows = [
        event(
            "memory_lookup_result",
            dict(memory_lookup_id="lookup", retrieval_status="timeout", adopted=True),
        ),
        event(
            "memory_context_complete",
            dict(
                memory_lookup_id="lookup",
                content_kind="returned",
                part_count=2,
                truncated=True,
            ),
        ),
    ]
    item = memory_evidence(rows)["lookup"]
    assert item["returned"]["complete"] is False
    assert item["returned"]["truncated"] is True
    assert "超时" in item["label"]


def test_timeline_keeps_discarded_attempt_and_attaches_memory_by_id():
    rows = [
        event("memory_request_started", dict(memory_lookup_id="lookup")),
        event("memory_request_finished", {}, at=2),
        event(
            "memory_lookup_result",
            dict(memory_lookup_id="lookup", retrieval_status="miss", adopted=True),
        ),
        event(
            "llm_request_started",
            dict(pipeline_attempt_id="a", pipeline_role="speculative"),
            span="llm",
            at=1,
        ),
        event("llm_request_finished", {}, span="llm", at=3),
        event(
            "llm_candidate_discarded",
            dict(pipeline_attempt_id="a", reason="guardrail_blocked"),
            span="chat",
            at=2,
        ),
    ]
    assert candidate_decisions(rows)["a"]["selection"] == "discarded"
    spans, _ = request_spans(rows)
    lanes = group_timeline_spans(spans, rows)
    memory = next(x for x in lanes if x["category"] == "memory_request")
    assert "未命中" in memory["label"]
    assert memory["segments"][0]["memory_evidence"]["lookup_id"] == "lookup"
    llm = next(x for x in lanes if x["category"] == "llm_request")
    assert "已弃用" in llm["label"]
    assert any(m["event"] == "llm_candidate_discarded" for m in llm["markers"])


def test_content_is_plain_data_not_html():
    rows = [
        event(
            "memory_lookup_result",
            dict(memory_lookup_id="x", retrieval_status="hit", adopted=True),
        ),
        event(
            "memory_context_part",
            dict(
                memory_lookup_id="x",
                content_kind="injected",
                part_index=0,
                text='<script>alert("x")</script>',
            ),
        ),
        event(
            "memory_context_complete",
            dict(
                memory_lookup_id="x",
                content_kind="injected",
                part_count=1,
                truncated=False,
            ),
        ),
    ]
    assert memory_evidence(rows)["x"]["injected"]["text"].startswith("<script>")


def test_cancel_request_transport_end_and_guardrail_replacement_stay_distinct():
    rows = [
        event("llm_request_started", dict(pipeline_attempt_id="a"), span="a", at=1),
        event(
            "llm_candidate_cancel_requested",
            dict(pipeline_attempt_id="a", reason="memory_changed"),
            at=2,
        ),
        event(
            "llm_candidate_discarded",
            dict(pipeline_attempt_id="a", reason="memory_changed"),
            at=2,
        ),
        event(
            "llm_candidate_transport_finished",
            dict(pipeline_attempt_id="a", cancelled=True),
            span="a",
            at=3,
        ),
        event("llm_request_finished", {}, span="a", at=3),
        event(
            "llm_request_started",
            dict(pipeline_attempt_id="c", pipeline_role="guardrail_replacement"),
            span="c",
            at=2,
        ),
        event("llm_candidate_adopted", dict(pipeline_attempt_id="c"), at=4),
        event("llm_request_finished", {}, span="c", at=4),
    ]
    decisions = candidate_decisions(rows)
    assert decisions["a"]["selection"] == "discarded"
    assert decisions["a"]["cancel_requested_ns"] == 2_000_000_000
    assert decisions["a"]["transport_finished_ns"] == 3_000_000_000
    spans, _ = request_spans(rows)
    lanes = group_timeline_spans(spans, rows)
    assert "护栏回复重发" in lanes[1]["label"]
    markers = {m["event"] for m in lanes[0]["markers"]}
    assert {
        "llm_candidate_cancel_requested",
        "llm_candidate_transport_finished",
    } <= markers


def test_workflow_revision_overrides_candidate_adoption_without_discarding_next_turn():
    from voice_scenarios.memory_evidence import candidate_decisions

    def event(name, **data):
        return {"event": name, "monotonic_ns": 1, "clock_id": "vas", "data": data}

    events = [
        event(
            "llm_request_started",
            pipeline_attempt_id="A",
            utterance_id="u",
            workflow_version=1,
        ),
        event("llm_candidate_adopted", pipeline_attempt_id="A"),
        event(
            "llm_request_started",
            pipeline_attempt_id="B",
            utterance_id="u",
            workflow_version=2,
        ),
        event("llm_candidate_adopted", pipeline_attempt_id="B"),
        event(
            "workflow_cancel_requested",
            utterance_id="u",
            workflow_version=1,
            reason="asr_text_revised",
        ),
        event(
            "workflow_cancel_requested",
            utterance_id="u",
            workflow_version=2,
            reason="new_user_turn",
        ),
    ]
    decisions = candidate_decisions(events)
    assert decisions["A"]["selection"] == "discarded"
    assert decisions["B"]["selection"] == "adopted"
