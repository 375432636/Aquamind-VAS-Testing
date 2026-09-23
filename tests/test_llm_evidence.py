from voice_scenarios.llm_evidence import summarize_llm_requests


def event(name, at, request="h1", span="llm1", clock="server", **data):
    return dict(
        event=name,
        monotonic_ns=int(at * 1e9),
        span_id=span,
        clock_id=clock,
        data=dict(http_request_id=request, **data),
    )


def test_separates_connection_response_and_valid_output_per_attempt():
    events = [
        event("llm_request_started", 0, request=None, provider="openai", model="qwen"),
        event(
            "http_request_started",
            0.1,
            llm_request_seq=1,
            http_request_seq=1,
            retry_count=0,
        ),
        event("http_tcp_started", 0.1),
        event("http_tcp_finished", 0.2),
        event("http_tls_started", 0.2),
        event("http_tls_finished", 0.4),
        event("http_request_body_sent", 0.5),
        event("http_response_headers", 1.0),
        event("llm_first_sse", 1.1),
        event("llm_first_token", 1.4, delta_kind="tool"),
        event("llm_usage", 1.5, input_tokens=100, cached_tokens=0, output_tokens=12),
        event("http_request_finished", 1.6, connection_state="new"),
        event(
            "http_request_started",
            2,
            request="h2",
            llm_request_seq=1,
            http_request_seq=2,
            retry_count=1,
        ),
        event("http_request_finished", 3, request="h2", connection_state="reused"),
    ]
    rows = summarize_llm_requests(events)
    assert len(rows) == 2
    assert rows[0]["tcp_ms"] == 100
    assert rows[0]["tls_ms"] == 200
    assert rows[0]["sent_to_headers_ms"] == 500
    assert rows[0]["first_sse_to_output_ms"] == 300
    assert rows[0]["cached_tokens"] == 0
    assert rows[1]["cached_tokens"] is None
    assert rows[1]["retry_count"] == 1
    assert rows[1]["tcp_ms"] is None
    assert rows[1]["first_sse_to_output_ms"] is None


def test_missing_usage_legacy_connection_and_cross_clock_are_unknown():
    rows = summarize_llm_requests(
        [
            event("llm_request_started", 0, request=None),
            event("http_request_started", 0.1),
            event("http_request_body_sent", 0.2),
            event("http_response_headers", 0.3, clock="another-server"),
            event("llm_first_token", 0.4, request=None),
            event("llm_request_started", 1, request=None, span="legacy"),
            event("llm_first_token", 1.2, request=None, span="legacy"),
        ]
    )
    assert len(rows) == 2
    assert all(r["connection_state"] == "unknown" for r in rows)
    assert all(r["cached_tokens"] is None for r in rows)
    assert rows[0]["sent_to_headers_ms"] is None
    assert rows[0]["first_sse_to_output_ms"] is None
    assert rows[1]["evidence_status"] == "unknown"


def test_failed_tcp_retry_never_counts_backoff_as_connection_time():
    rows = summarize_llm_requests(
        [
            event("llm_request_started", 0, request=None),
            event("http_request_started", 0, transport="httpx"),
            event("http_tcp_started", 0.1),
            event("http_transport_error", 0.2, phase="connection.connect_tcp.failed"),
            event("http_transport_retry_started", 0.2, transport_retry_count=1),
            event("http_transport_retry_finished", 1, transport_retry_count=1),
            event("http_tcp_started", 1.1),
            event("http_tcp_finished", 1.2),
            event("http_request_finished", 1.3, transport_retry_count=1),
        ]
    )
    assert rows[0]["tcp_ms"] is None
    assert rows[0]["tcp_attempts"][0]["duration_ms"] is None
    assert rows[0]["tcp_attempts"][1]["duration_ms"] == 100
    assert rows[0]["transport_retry_count"] == 1


def test_report_has_attempt_rows_unknown_tokens_and_separate_sse_markers(tmp_path):
    from voice_scenarios.report import build_report, evaluate

    result = dict(
        name="LLM evidence",
        status="passed",
        turns=[dict(id="one", status="completed", events=[])],
    )
    events = [
        event("llm_request_started", 0, request=None),
        event("http_request_started", 0.1, llm_request_seq=1, http_request_seq=1),
        event("llm_first_sse", 0.2),
        event("llm_first_token", 0.4),
        event("http_request_finished", 0.5, connection_state="unknown"),
        event("llm_request_finished", 0.6, request=None),
    ]
    for row in events:
        row["listen_turn_id"] = 1
    report = evaluate(result, events)
    assert report["turns"][0]["llm_requests"][0]["first_sse_to_output_ms"] == 200
    assert [s["name"] for s in report["turns"][0]["spans"]] == ["llm_request"]
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "turn-001.html").read_text()
    assert "输入 / 缓存 / 输出 Token" in page
    assert "首个完整 SSE 数据块" in page
    assert "服务端等待不等于模型计算" in page
    assert "未知 / 未知 / 未知" in page
