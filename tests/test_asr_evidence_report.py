from voice_scenarios.report import build_report, evaluate


def test_asr_settings_and_endpoint_arrival_keep_separate_clock_domains(tmp_path):
    endpoint = dict(
        event="asr_endpoint_detected",
        listen_turn_id=1,
        span_id="asr",
        clock_id="vas",
        monotonic_ns=100_000_000_000,
        data={},
    )
    result = dict(
        name="vad",
        status="passed",
        turns=[
            dict(
                id="short",
                status="completed",
                input_settings={"mode": "vad"},
                events=[
                    dict(event="speech_input_finished", at_ns=1_000_000_000, data={}),
                    dict(event="vas_event", at_ns=1_600_000_000, data=endpoint),
                ],
            )
        ],
    )
    events = [
        dict(
            event="asr_session_config_requested",
            listen_turn_id=1,
            span_id="asr",
            clock_id="vas",
            monotonic_ns=99_000_000_000,
            data={"requested_silence_duration_ms": 400, "vad_enabled": True},
        ),
        dict(
            event="asr_session_config_confirmed",
            listen_turn_id=1,
            span_id="asr",
            clock_id="vas",
            monotonic_ns=99_100_000_000,
            data={"confirmed_silence_duration_ms": None},
        ),
        endpoint,
    ]
    report = evaluate(result, events)
    turn = report["turns"][0]
    assert turn["metrics"]["input_finish_to_asr_endpoint_ms"] is None
    assert turn["metrics"]["input_finish_to_asr_endpoint_observed_ms"] == 600
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "turn-001.html").read_text()
    assert "请求 400 ms" in page
    assert "服务端确认 未知" in page
    assert "含诊断推送与网络延迟" in page
