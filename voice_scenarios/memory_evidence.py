"""Reconstruct explicitly captured Memory results and LLM admission decisions."""

MEMORY_LABELS = {
    "hit": "命中",
    "miss": "未命中",
    "duplicate": "仅重复画像",
    "timeout": "超时 · 无记忆降级",
    "error": "失败 · 无记忆降级",
    "capacity": "查询容量已满 · 无记忆降级",
    "disabled": "未启用",
    "cancelled": "已取消",
}


def candidate_decisions(events):
    result = {}
    for event in events:
        name = event.get("event")
        data = event.get("data", {})
        key = data.get("pipeline_attempt_id")
        timestamp_fields = {
            "llm_candidate_adopted": "decision_ns",
            "llm_candidate_discarded": "decision_ns",
            "llm_candidate_released": "released_ns",
            "llm_candidate_cancel_requested": "cancel_requested_ns",
            "llm_candidate_transport_finished": "transport_finished_ns",
        }
        if key and name in timestamp_fields:
            previous = result.setdefault(key, {})
            previous.update(data)
            if name in {"llm_candidate_adopted", "llm_candidate_discarded"}:
                previous["selection"] = (
                    "adopted" if name == "llm_candidate_adopted" else "discarded"
                )
            previous[timestamp_fields[name]] = event.get("monotonic_ns")
    # A Memory candidate can be adopted and subsequently invalidated when the
    # cumulative ASR text changes. Keep it on the detailed trace, not as a
    # successful first response. Finishing a normal turn is not a discard.
    revoked = {}
    for event in events:
        data = event.get("data", {})
        if event.get("event") == "workflow_cancel_requested" and data.get("reason") in {
            "speech_continuation",
            "asr_text_revised",
            "empty_asr_final",
            "asr_final_cancelled",
            "asr_final_timeout",
            "asr_final_error",
            "workflow_error",
            "answer_buffer_limit",
        }:
            revoked[
                event.get("clock_id"),
                data.get("utterance_id"),
                data.get("workflow_version"),
            ] = event
    for event in events:
        data = event.get("data", {})
        key = data.get("pipeline_attempt_id")
        cancelled = revoked.get(
            (
                event.get("clock_id"),
                data.get("utterance_id"),
                data.get("workflow_version"),
            )
        )
        if event.get("event") == "llm_request_started" and key and cancelled:
            result.setdefault(key, {}).update(
                selection="discarded",
                reason=cancelled["data"]["reason"],
                workflow_cancelled_ns=cancelled.get("monotonic_ns"),
            )
    return result


def memory_evidence(events):
    """Missing evidence is unknown, never a miss; fragment loss stays visible."""
    result, fragments, endings = {}, {}, {}
    for event in events:
        data = event.get("data", {})
        lookup = data.get("memory_lookup_id")
        if not lookup:
            continue
        key = (lookup, data.get("content_kind"))
        if event.get("event") == "memory_lookup_result":
            status = data.get("retrieval_status")
            result[lookup] = dict(
                data, lookup_id=lookup, label=MEMORY_LABELS.get(status, "未采集")
            )
            if status == "hit":
                result[lookup]["label"] += (
                    " · 已采用" if data.get("adopted") else " · 未采用"
                )
        elif event.get("event") == "memory_context_part":
            index = data.get("part_index")
            if type(index) is int and index >= 0 and isinstance(data.get("text"), str):
                fragments.setdefault(key, {})[index] = data["text"]
        elif event.get("event") == "memory_context_complete":
            endings[key] = data
    for lookup, item in result.items():
        for kind in ("returned", "injected"):
            key = (lookup, kind)
            parts, end = fragments.get(key, {}), endings.get(key, {})
            count = end.get("part_count")
            complete = (
                type(count) is int
                and 0 <= count <= 128
                and set(parts) == set(range(count))
            )
            item[kind] = dict(
                text="".join(parts[i] for i in sorted(parts)),
                complete=complete,
                truncated=end.get("truncated", False),
                total_bytes=end.get("total_bytes"),
                captured_bytes=end.get("captured_bytes"),
            )
    return result
