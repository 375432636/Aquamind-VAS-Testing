from voice_scenarios.clock_sync import ClockSyncSamples


def sample(offset=120_000_000, phase="start", session="s"):
    return dict(
        t1=1_000_000_000,
        t2=1_020_000_000 + offset,
        t3=1_021_000_000 + offset,
        t4=1_041_000_000,
        phase=phase,
        server_session_id=session,
    )


def test_offset_rtt_and_uncertainty():
    result = ClockSyncSamples([sample()]).summary()
    assert result["status"] == "calibrated"
    assert result["offset_ns"] == 120_000_000
    assert result["rtt_ns"] == 40_000_000
    assert result["uncertainty_ns"] == 20_000_000


def test_drift_and_different_sessions_are_not_silently_aligned():
    assert (
        ClockSyncSamples([sample(), sample(300_000_000, "end")]).summary()["status"]
        == "unstable"
    )
    assert (
        ClockSyncSamples([sample(), sample(session="other")]).summary()["status"]
        == "unstable"
    )


def test_invalid_and_missing_samples_fall_back():
    invalid = sample()
    invalid["t4"] = 0
    assert ClockSyncSamples([invalid]).summary()["status"] == "unavailable"
    assert ClockSyncSamples([]).summary()["status"] == "unavailable"


def test_report_moves_only_server_coordinates_and_preserves_raw_data(tmp_path):
    from voice_scenarios.clock_timeline import combined_timeline
    from voice_scenarios.report import build_report, evaluate

    clock = dict(monotonic_ns=0, wall_time_ns=1_000_000_000)
    turn = dict(
        events=[],
        timeline_lanes=[
            dict(
                segments=[],
                markers=[dict(start_ns=10, clock_id="vas", event="asr_final")],
            )
        ],
        vas_events=[dict(monotonic_ns=10, clock_id="vas", wall_time_ns=2_000_000_000)],
    )
    sync = ClockSyncSamples([sample()]).summary()
    raw = combined_timeline(turn, clock)
    aligned = combined_timeline(turn, clock, sync)
    assert raw["origin_wall_time_ns"] == aligned["origin_wall_time_ns"]
    assert (
        raw["lanes"][0]["markers"][0]["plot_start_ns"]
        - aligned["lanes"][0]["markers"][0]["plot_start_ns"]
        == 120_000_000
    )
    assert turn["vas_events"][0]["wall_time_ns"] == 2_000_000_000
    result = dict(name="clock", status="passed", turns=[], clock_sync=sync)
    build_report(evaluate(result, []), tmp_path / "report.html")
    html = (tmp_path / "report.html").read_text()
    assert "跨端已校准" in html and "?clock=raw" in html and "raw_view" in html


def test_python_probe_timeout_is_optional():
    import asyncio
    import time

    from voice_scenarios.clock_sync import collect_clock_samples

    async def run():
        sent, pending = [], {}

        async def send(message):
            sent.append(message)

        anchor = dict(monotonic_ns=time.monotonic_ns(), wall_time_ns=time.time_ns())
        assert await collect_clock_samples(send, pending, anchor, budget=0.02) == []
        assert not pending and len(sent) == 1

    asyncio.run(run())


def test_timeline_subtitle_reports_applied_calibration():
    from voice_scenarios.report import _session_panel

    playback = {
        "vas_timeline": {"mode": "wall", "clock_sync": {"status": "calibrated"}}
    }
    html = _session_panel({"session_playback": playback})
    assert 'id="session-clock-label"' in html
    assert "VAS 已校准至客户端" in html
    assert "两端未校时" not in html
    playback["vas_timeline"]["clock_sync"] = None
    assert "两端未校时" in _session_panel({"session_playback": playback})


def test_python_probe_correlates_and_keeps_five_samples():
    import asyncio
    import time

    from voice_scenarios.clock_sync import collect_clock_samples

    async def run():
        pending = {}
        anchor = dict(monotonic_ns=time.monotonic_ns(), wall_time_ns=time.time_ns())

        async def send(message):
            now = time.monotonic_ns()
            wall = anchor["wall_time_ns"] + now - anchor["monotonic_ns"]
            pending[message["request_id"]].set_result(
                (
                    dict(
                        server_received_ns=str(wall),
                        server_sent_ns=str(wall),
                        server_session_id="s",
                    ),
                    now,
                )
            )

        samples = await collect_clock_samples(send, pending, anchor)
        assert len(samples) == 5 and not pending
        assert ClockSyncSamples(samples).summary()["status"] == "calibrated"

    asyncio.run(run())


def test_early_or_broken_vas_clock_cannot_change_client_coordinates():
    from test_clock_timeline import example

    from voice_scenarios.clock_timeline import combined_timeline

    turn, clock = example()

    def client(chart):
        return [lane for lane in chart["lanes"] if lane["source"] == "client"]

    baseline = client(combined_timeline(turn, clock))
    # Force VAS records before connection; client zero must remain its own anchor.
    for event in turn["vas_events"]:
        event["wall_time_ns"] -= 30_000_000_000
    assert (
        client(combined_timeline(turn, clock, ClockSyncSamples([sample()]).summary()))
        == baseline
    )
    turn["vas_events"][0].pop("wall_time_ns")
    assert client(combined_timeline(turn, clock)) == baseline
