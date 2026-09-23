from voice_scenarios.input_control import inspect_input_control, refresh_ptt_markers


def event(name, at=0, **data):
    return dict(event=name, monotonic_ns=at, clock_id="vas", data=data)


def test_manual_stop_does_not_mean_all_vad_is_disabled():
    turn = dict(
        input_settings={"mode": "manual"},
        events=[dict(event="listen_stop_sent", at_ns=100)],
        vas_events=[
            event("listen_start_received", mode="manual"),
            event("asr_session_config_requested", vad_enabled=False),
            event("asr_session_config_confirmed", vad_enabled=None),
            event("local_vad_endpoint_detected", 90),
            event("listen_stop_received", 100),
            event("asr_commit_sent", 101),
            event("asr_final", 120),
        ],
    )
    result = inspect_input_control(turn)
    assert result["stop_sent"] == result["stop_received"] == 1
    assert result["requested_vad"] == "off"
    assert result["confirmed_vad"] == "unknown"
    assert result["local_vad"] == "observed"
    assert result["finalization"] == "manual_commit"


def test_missing_vad_events_are_unknown_and_never_proof_of_disabled_vad():
    result = inspect_input_control(dict(input_settings={"mode": "manual"}))
    assert result["local_vad"] == result["confirmed_vad"] == "unknown"
    assert result["stop_received"] == 0
    assert result["finalization"] == "unknown"


def test_detects_early_final_without_comparing_client_and_server_clocks():
    result = inspect_input_control(
        dict(
            input_settings={"mode": "manual"},
            events=[dict(event="listen_stop_sent", at_ns=999999999999)],
            vas_events=[event("asr_final", 5), event("listen_stop_received", 10)],
        )
    )
    assert result["finalization"] == "early_final"


def test_conflicting_or_incomplete_provider_confirmations_are_not_off():
    base = dict(input_settings={"mode": "manual"})
    result = inspect_input_control(
        dict(
            base,
            vas_events=[
                event("asr_session_config_confirmed", vad_enabled=False),
                event("asr_session_config_confirmed", vad_enabled=True),
            ],
        )
    )
    assert result["confirmed_vad"] == "on"
    result = inspect_input_control(
        dict(
            base,
            vas_events=[
                event("asr_session_config_confirmed", vad_enabled=False),
                event("asr_session_config_confirmed", vad_enabled=None),
            ],
        )
    )
    assert result["confirmed_vad"] == "unknown"


def test_manual_finalization_does_not_compare_different_server_clocks():
    stop = event("listen_stop_received", 1)
    commit = event("asr_commit_sent", 2)
    final = event("asr_final", 3)
    final["clock_id"] = "other-vas"
    result = inspect_input_control(
        dict(input_settings={"mode": "manual"}, vas_events=[stop, commit, final])
    )
    assert result["finalization"] == "unknown"


def test_ptt_markers_keep_actual_control_times_and_replace_duplicate_input_end():
    playback = {
        "zero_at_ns": 1_000_000_000,
        "markers": [
            dict(turn_index=1, kind="input_end", at_seconds=3.456789),
            dict(turn_index=1, kind="first_playback", at_seconds=4),
        ],
    }
    turns = [
        dict(
            input_settings={"mode": "manual"},
            events=[
                dict(event="listen_start_sent", at_ns=2_000_123_000),
                dict(event="listen_stop_sent", at_ns=4_456_789_000),
            ],
        )
    ]
    refresh_ptt_markers(playback, turns)
    expected = [
        dict(turn_index=1, kind="first_playback", at_seconds=4),
        dict(turn_index=1, kind="ptt_start", at_seconds=1.000123),
        dict(turn_index=1, kind="ptt_stop", at_seconds=3.456789),
    ]
    assert playback["markers"] == expected
    refresh_ptt_markers(playback, turns)
    assert playback["markers"] == expected


def test_ptt_does_not_invent_end_when_stop_fails_or_mark_text_and_vad_as_ptt():
    events = [
        dict(event="listen_start_sent", at_ns=1_000_000_000),
        dict(event="listen_stop_failed", at_ns=2_000_000_000),
    ]
    turns = [
        dict(input_settings={"mode": "manual"}, events=events),
        dict(input_type="text", input_settings={"mode": "manual"}, events=events),
        dict(sensor="touch_head", input_settings={"mode": "manual"}, events=events),
        dict(input_settings={"mode": "vad"}, events=events),
    ]
    playback = {"zero_at_ns": 0}
    refresh_ptt_markers(playback, turns)
    assert playback["markers"] == [dict(turn_index=1, kind="ptt_start", at_seconds=1)]
    unavailable = {"zero_at_ns": None}
    refresh_ptt_markers(unavailable, turns)
    assert not unavailable.get("markers")
