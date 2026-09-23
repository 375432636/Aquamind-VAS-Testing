"""Summarize observed LLM attempts without inferring unobserved network stages."""

from .memory_evidence import candidate_decisions


def thinking_mode(data):
    """Label only an explicitly recorded request flag; never infer from model."""
    parameters = (data or {}).get("parameters")
    value = parameters.get("enable_thinking") if isinstance(parameters, dict) else None
    if value is True:
        return "Thinking"
    if value is False:
        return "Unthinking"
    return "未采集"


def _duration(events, start, end):
    a = next((e for e in events if e["event"] == start), None)
    b = next((e for e in events if e["event"] == end), None)
    if not a or not b or not a.get("clock_id") or a["clock_id"] != b.get("clock_id"):
        return None
    if type(a.get("monotonic_ns")) is not int or type(b.get("monotonic_ns")) is not int:
        return None
    value = b["monotonic_ns"] - a["monotonic_ns"]
    return value / 1e6 if value >= 0 else None


def _connection_attempts(events, phase):
    attempts = []
    active = None
    for event in events:
        if event["event"] == f"http_{phase}_started":
            active = {"start": event, "duration_ms": None}
            attempts.append(active)
        elif event["event"] == f"http_{phase}_finished" and active is not None:
            active["duration_ms"] = _duration(
                [active["start"], event],
                f"http_{phase}_started",
                f"http_{phase}_finished",
            )
            active = None
        elif event["event"] == "http_transport_error":
            failed_phase = event.get("data", {}).get("phase", "")
            if (phase == "tcp" and "connect_tcp" in failed_phase) or (
                phase == "tls" and "start_tls" in failed_phase
            ):
                active = None
    return [
        {"duration_ms": a["duration_ms"], "start_ns": a["start"].get("monotonic_ns")}
        for a in attempts
    ]


def summarize_llm_requests(events):
    """One row per physical request, including retries, errors and cancellation.

    Logical requests on older servers retain a row with unknown HTTP evidence.
    No duration is calculated across clocks, attempts or logical request spans.
    """
    decisions = candidate_decisions(events)
    logical = {
        e["span_id"]: e
        for e in events
        if e["event"] == "llm_request_started" and e.get("span_id")
    }
    groups = {}
    for e in sorted(events, key=lambda e: e.get("monotonic_ns", 0)):
        data = e.get("data", {})
        sid, rid = e.get("span_id"), data.get("http_request_id")
        if rid and (sid in logical or data.get("transport") == "httpx"):
            groups.setdefault((sid, rid), []).append(e)
    observed = {sid for sid, _ in groups}
    for sid, start in logical.items():
        if sid not in observed:
            groups[(sid, None)] = [e for e in events if e.get("span_id") == sid]
    result = []
    for (sid, rid), rows in groups.items():
        metadata = dict(logical.get(sid, {}).get("data", {}))
        for e in rows:
            metadata.update(e.get("data", {}))
        summary = {
            "span_id": sid,
            "http_request_id": rid,
            "llm_request_seq": metadata.get("llm_request_seq"),
            "http_request_seq": metadata.get("http_request_seq"),
            "retry_count": metadata.get("retry_count"),
            "transport_retry_count": metadata.get("transport_retry_count"),
            "provider": metadata.get("vendor") or metadata.get("provider"),
            "model": metadata.get("model"),
            "thinking_mode": thinking_mode(logical.get(sid, {}).get("data")),
            "connection_state": metadata.get("connection_state", "unknown"),
            "connection_evidence": metadata.get("connection_evidence"),
            "http_status": metadata.get("http_status"),
            "status": next(
                (e.get("status") for e in reversed(rows) if e.get("status")),
                "unknown",
            ),
        }
        summary.update(
            {
                k: metadata.get(k)
                for k in (
                    "pipeline_attempt_id",
                    "pipeline_role",
                    "replaces_attempt_id",
                )
            }
        )
        summary.update(decisions.get(metadata.get("pipeline_attempt_id"), {}))
        for name in ("input_tokens", "cached_tokens", "output_tokens"):
            value = metadata.get(name)
            summary[name] = (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else None
            )
        for name, start, end in (
            ("tcp_ms", "http_tcp_started", "http_tcp_finished"),
            ("tls_ms", "http_tls_started", "http_tls_finished"),
            ("sent_to_headers_ms", "http_request_body_sent", "http_response_headers"),
            ("headers_to_first_sse_ms", "http_response_headers", "llm_first_sse"),
            ("first_sse_to_output_ms", "llm_first_sse", "llm_first_token"),
        ):
            summary[name] = _duration(rows, start, end) if rid else None
        for phase in ("tcp", "tls"):
            attempts = _connection_attempts(rows, phase) if rid else []
            summary[f"{phase}_attempts"] = attempts
            summary[f"{phase}_ms"] = (
                sum(a["duration_ms"] for a in attempts)
                if attempts and all(a["duration_ms"] is not None for a in attempts)
                else None
            )
        if rid and any(e["event"] == "llm_first_output" for e in rows):
            summary["first_sse_to_output_ms"] = _duration(
                rows, "llm_first_sse", "llm_first_output"
            )
        summary["evidence_status"] = "observed" if rid else "unknown"
        result.append(summary)
    return result
